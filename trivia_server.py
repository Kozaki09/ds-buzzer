import threading
import time
import subprocess
import sys
import os
import socket
import json
from lamport_clock import LamportClock
from utils import (
    create_server_socket,
    accept_json_connection,
    request_response,
    ConnectionManager,
    start_receiver_thread,
    NetworkError
)

round_active = False
current_winner = None
buzz_lock = threading.Lock()

current_answer = None
answer_event = threading.Event()

HOST = "0.0.0.0"
PORT = 5000

clock = LamportClock()
clients = ConnectionManager()

questions = [
    {"question": "What is the capital of France?", "answer": "Paris"},
    {"question": "What is the largest planet in our solar system?", "answer": "Jupiter"},
    {"question": "What year did the Titanic sink?", "answer": "1912"}
]

def get_local_ip():
    """Tricks the OS into revealing its real local IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't have to be reachable, just forces the OS to route an external IP
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def find_naming_server(max_retries=None):
    """Broadcasts a shout to the local network to find the Naming Server."""
    print("[NETWORK] Searching for Naming Server on LAN...")
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_sock.settimeout(2.0) # Wait 2 seconds for a reply
    
    attempts = 0
    while True:
        try:
            # Shout to everyone on the network
            udp_sock.sendto(b"WHERE_IS_NAMING_SERVER", ("<broadcast>", 4001))
            
            # Listen for the reply
            data, addr = udp_sock.recvfrom(1024)
            info = json.loads(data.decode())
            
            host, port = info["host"], info["port"]
            print(f"[NETWORK] Found Naming Server at {host}:{port}")
            return host, port
            
        except socket.timeout:
            attempts += 1
            if max_retries and attempts >= max_retries:
                return None, None # Give up so we can auto-start it
            print("[NETWORK] Still searching...")

def register_with_naming_server():
    global NAMING_HOST, NAMING_PORT
    
    # 1. Search for the naming server (give up after 2 attempts / 4 seconds)
    NAMING_HOST, NAMING_PORT = find_naming_server(max_retries=2)

    # 2. If it wasn't found, start it automatically
    if not NAMING_HOST:
        print("[SERVER] Naming Server not found. Starting it automatically...")
        subprocess.Popen(
            [sys.executable, "naming_server.py"], 
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
        )
        time.sleep(2) # Give it time to boot
        
        # Try finding it again (this time loop indefinitely until it answers)
        NAMING_HOST, NAMING_PORT = find_naming_server()

    # 3. Register our TCP Trivia Service with it
    registration_msg = {
        "type": "register",
        "service": "trivia.server.main",
        "host": get_local_ip(), 
        "port": PORT
    }

    try:
        response = request_response(NAMING_HOST, NAMING_PORT, registration_msg)
        print(f"[SERVER] Registered successfully: {response}")
    except Exception as e:
        print(f"[SERVER] FATAL ERROR: Failed to register TCP server: {e}")
        sys.exit(1)

def heartbeat_loop():
    """Acts like a DNS refresh, keeping our record alive."""
    registration_msg = {
        "type": "register",
        "service": "trivia.server.main",
        "host": get_local_ip(), 
        "port": PORT
    }
    
    while True:
        time.sleep(10) # Send heartbeat every 10 seconds
        try:
            request_response(NAMING_HOST, NAMING_PORT, registration_msg)
        except Exception:
            # If the Naming Server crashed, we will try to find it again!
            print("[SERVER] Lost connection to Naming Server. Re-discovering...")
            # We call the full register function which handles auto-starting
            register_with_naming_server()

def handle_client_message(conn, msg):
    global round_active, current_winner
    msg_type = msg.get("type")
    
    # -------------------------
    # NEW PLAYER JOINED
    # -------------------------
    if msg_type == "JOIN":
        player_id = msg.get("payload", {}).get("player_id", "Unknown")
        print(f"[SERVER] {player_id} joined the game.")
        return

    # -------------------------
    # BUZZ RECEIVED
    # -------------------------
    if msg_type == "BUZZ":
        payload = msg.get("payload", {})
        player_id = payload.get("player_id")
        client_time = payload.get("lamport_time", 0)
        
        clock.update(client_time)
        won = False
        
        with buzz_lock:
            if round_active:
                round_active = False
                current_winner = player_id
                won = True

        if won:
            print(f"[SERVER] {player_id} buzzed in first!")
            
            # Send 'You Won' specifically to the buzzer
            conn.send({
                "type": "WINNER",
                "payload": {"you_won": True, "lamport_time": clock.increment()}
            })
            
            # Broadcast the winner to everyone else
            clients.broadcast({
                "type": "WINNER",
                "payload": {"winner": player_id, "lamport_time": clock.increment()}
            }, exclude=conn)
            
        else:
            # Send rejection to late buzzers
            conn.send({
                "type": "WINNER",
                "payload": {"you_won": False, "winner": current_winner, "lamport_time": clock.increment()}
            })

    # -------------------------
    # ANSWER RECEIVED
    # -------------------------
    elif msg_type == "ANSWER":
        player_id = msg.get("payload", {}).get("player_id")
        ans = msg.get("payload", {}).get("answer")
        
        if player_id == current_winner:
            print(f"[SERVER] {player_id} answered: {ans}")
            global current_answer
            current_answer = ans
            answer_event.set()
        else:
            print(f"[SERVER] Ignored late answer from {player_id}.")

def handle_disconnect(conn, error):
    clients.remove(conn)
    print(f"[SERVER] Client disconnected: {conn.label}")

def start_tcp_server():
    server = create_server_socket(HOST, PORT)
    print(f"[SERVER] TCP Listening on {HOST}:{PORT}")
    
    while True:
        try:
            conn = accept_json_connection(server)
            clients.add(conn)
            # Spawn a background thread to listen to this specific client
            start_receiver_thread(conn, handle_client_message, handle_disconnect)
        except Exception as e:
            print(f"[SERVER] Accept error: {e}")

def game_loop():
    global round_active, current_winner, current_answer
    question_number = 1
    
    # -------------------------
    # 1. LOBBY WAITING ROOM
    # -------------------------
    print("[SERVER] Waiting for at least 2 players to join...")
    while clients.count() < 2:
        time.sleep(1)

    print("[SERVER] 2 players reached! Waiting 10 seconds for additional players...")
    clients.broadcast({
        "type": "START", 
        "payload": {"message": "Minimum players reached! Game starts in 10 seconds..."}
    })
    time.sleep(10)

    clients.broadcast({"type": "START", "payload": {"message": "Trivia game starting now!"}})
    time.sleep(2)

    # -------------------------
    # 2. MAIN TRIVIA LOOP
    # -------------------------
    for q in questions:
        print(f"\n[SERVER] Sending Question {question_number}...")
        clock.increment()
        
        clients.broadcast({
            "type": "QUESTION",
            "question_number": question_number,
            "payload": {"question": q["question"], "timestamp": clock.get_time()}
        })

        round_active = True
        current_winner = None
        
        # Buzz-in Timer (10 seconds to buzz)
        timeout = 10
        start = time.time()
        
        while time.time() - start < timeout:
            if not round_active: 
                break # Someone buzzed!
            time.sleep(0.1)

        # -------------------------
        # 3. RESOLVE THE ROUND
        # -------------------------
        if round_active:
            # Time ran out, nobody buzzed
            round_active = False
            clients.broadcast({
                "type": "RESULT", 
                "payload": {"message": "Time's up! No one buzzed in.", "lamport_time": clock.increment()}
            })
            print("[SERVER] No buzz received.")
            time.sleep(3)
            
        else:
            # Someone buzzed, wait up to 10 seconds for their answer
            print(f"[SERVER] Waiting up to 10 seconds for {current_winner} to answer...")
            
            answer_event.clear()
            current_answer = None
            
            # This pauses the loop until answer_event.set() is called OR 10 seconds pass
            got_answer = answer_event.wait(timeout=10.0)

            if got_answer:
                result_msg = f"{current_winner} answered: {current_answer}"
            else:
                result_msg = f"{current_winner} ran out of time to answer!"
                print(f"[SERVER] {current_winner} timed out.")

            clients.broadcast({
                "type": "RESULT", 
                "payload": {"message": result_msg, "lamport_time": clock.increment()}
            })
            
            time.sleep(3) # Short pause so players can read the result before the next question

        question_number += 1

    clients.broadcast({"type": "END", "payload": {"message": "Game Over"}})
    print("\n[SERVER] Game finished.")

if __name__ == "__main__":
    print("[SERVER] Starting Trivia Host Server...")
    register_with_naming_server()
    
    threading.Thread(target=heartbeat_loop, daemon=True).start() 
    
    threading.Thread(target=start_tcp_server, daemon=True).start()
    game_loop()