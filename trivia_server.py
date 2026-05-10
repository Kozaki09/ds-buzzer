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

# IMPORT PROTOCOL FACTORIES
from messages import (
    MsgType, 
    make_register_msg, 
    make_start, 
    make_question, 
    make_winner, 
    make_result, 
    make_end
)

round_active = False
current_winner = None
buzz_lock = threading.Lock()

buzz_collection = []

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
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def find_naming_server(max_retries=None):
    print("[NETWORK] Searching for Naming Server on LAN...")
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_sock.settimeout(2.0) 
    
    attempts = 0
    while True:
        try:
            udp_sock.sendto(b"WHERE_IS_NAMING_SERVER", ("<broadcast>", 4001))
            data, addr = udp_sock.recvfrom(1024)
            info = json.loads(data.decode())
            
            host, port = info["host"], info["port"]
            print(f"[NETWORK] Found Naming Server at {host}:{port}")
            return host, port
            
        except socket.timeout:
            attempts += 1
            if max_retries and attempts >= max_retries:
                return None, None 
            print("[NETWORK] Still searching...")

def register_with_naming_server():
    global NAMING_HOST, NAMING_PORT
    NAMING_HOST, NAMING_PORT = find_naming_server(max_retries=2)

    if not NAMING_HOST:
        print("[SERVER] Naming Server not found. Starting it automatically...")
        subprocess.Popen(
            [sys.executable, "naming_server.py"], 
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
        )
        time.sleep(2) 
        NAMING_HOST, NAMING_PORT = find_naming_server()

    # REFACTORED: Use messages factory
    registration_msg = make_register_msg("trivia.server.main", get_local_ip(), PORT)

    try:
        response = request_response(NAMING_HOST, NAMING_PORT, registration_msg)
        print(f"[SERVER] Registered successfully: {response}")
    except Exception as e:
        print(f"[SERVER] FATAL ERROR: Failed to register TCP server: {e}")
        sys.exit(1)

def heartbeat_loop():
    # REFACTORED: Use messages factory
    registration_msg = make_register_msg("trivia.server.main", get_local_ip(), PORT)
    
    while True:
        time.sleep(10) 
        try:
            request_response(NAMING_HOST, NAMING_PORT, registration_msg)
        except Exception:
            print("[SERVER] Lost connection to Naming Server. Re-discovering...")
            register_with_naming_server()

def handle_client_message(conn, msg):
    global round_active, current_winner
    msg_type = msg.get("type")
    
    # REFACTORED: Use MsgType constants
    if msg_type == MsgType.JOIN:
        player_id = msg.get("payload", {}).get("player_id", "Unknown")
        print(f"[SERVER] {player_id} joined the game.")
        return

    if msg_type == MsgType.BUZZ:
        payload = msg.get("payload", {})
        player_id = payload.get("player_id")
        client_time = payload.get("lamport_time", 0)
        
        clock.update(client_time)
        
        with buzz_lock:
            if round_active:
                arrival_time = time.time() 
                buzz_collection.append((client_time, arrival_time, player_id, conn))
                print(f"[SERVER] Received buzz from {player_id} (Lamport: {client_time})")

    elif msg_type == MsgType.ANSWER:
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
            start_receiver_thread(conn, handle_client_message, handle_disconnect)
        except Exception as e:
            print(f"[SERVER] Accept error: {e}")

def game_loop():
    global round_active, current_winner, current_answer
    question_number = 1
    
    print("[SERVER] Waiting for at least 2 players to join...")
    while clients.count() < 2:
        time.sleep(1)

    print("[SERVER] 2 players reached! Waiting 10 seconds for additional players...")
    # REFACTORED: Use messages factory
    clients.broadcast(make_start("Minimum players reached! Game starts in 10 seconds..."))
    time.sleep(10)

    clients.broadcast(make_start("Trivia game starting now!"))
    time.sleep(2)

    for q in questions:
        print(f"\n[SERVER] Sending Question {question_number}...")
        clock.increment()
        
        clients.broadcast(make_question(question_number, q["question"], clock.get_time()))

        round_active = True
        current_winner = None
        buzz_collection.clear() 
        
        timeout = 10
        start = time.time()

        first_buzz_physical_time = None
        
        # 1. THE WAITING PHASE
        while time.time() - start < timeout:
            with buzz_lock:
                if len(buzz_collection) > 0:
                    if first_buzz_physical_time is None:
                        first_buzz_physical_time = time.time()
                    
                    # The "Lamport Window": Wait 250ms for slower network packets
                    if time.time() - first_buzz_physical_time >= 0.25:
                        round_active = False
                        break
            time.sleep(0.05)

        # 2. THE RESOLUTION PHASE
        if len(buzz_collection) > 0:
            round_active = False
            
            # Sort by Lamport time first (x[0]). 
            # If tied, sort by physical arrival time (x[1]) instead of alphabetically!
            buzz_collection.sort(key=lambda x: (x[0], x[1]))
            
            winning_time, winning_arrival, current_winner, winning_conn = buzz_collection[0]

            print(f"[SERVER] {current_winner} won the race! (Lamport: {winning_time})")
            
            # Send WINNER messages
            winning_conn.send(make_winner(you_won=True, lamport_time=clock.increment()))
            clients.broadcast(
                make_winner(you_won=False, lamport_time=clock.increment(), winner_name=current_winner), 
                exclude=winning_conn
            )

            # Wait for their answer
            print(f"[SERVER] Waiting up to 10 seconds for {current_winner} to answer...")
            answer_event.clear()
            current_answer = None
            got_answer = answer_event.wait(timeout=10.0)

            if got_answer:
                result_msg = f"{current_winner} answered: {current_answer}"
            else:
                result_msg = f"{current_winner} ran out of time to answer!"
                print(f"[SERVER] {current_winner} timed out.")

            clients.broadcast(make_result(result_msg, clock.increment()))
            time.sleep(3)
            
        else:
            # Time ran out, nobody buzzed
            round_active = False
            clients.broadcast(make_result("Time's up! No one buzzed in.", clock.increment()))
            print("[SERVER] No buzz received.")
            time.sleep(3)

        question_number += 1

    # REFACTORED: Use messages factory
    clients.broadcast(make_end("Game Over"))
    print("\n[SERVER] Game finished.")

if __name__ == "__main__":
    print("[SERVER] Starting Trivia Host Server...")
    register_with_naming_server()
    threading.Thread(target=heartbeat_loop, daemon=True).start() 
    threading.Thread(target=start_tcp_server, daemon=True).start()
    game_loop()