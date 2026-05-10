import threading
import time
from lamport_clock import LamportClock
from utils import (
    create_server_socket,
    accept_json_connection,
    request_response,
    ConnectionManager,
    start_receiver_thread
)

NAMING_HOST = "127.0.0.1"
NAMING_PORT = 4000
HOST = "0.0.0.0"
PORT = 5000

clock = LamportClock()
clients = ConnectionManager()

round_active = False
current_winner = None
buzz_lock = threading.Lock()

questions = [
    {"question": "What is the capital of France?", "answer": "Paris"},
    {"question": "What is the largest planet in our solar system?", "answer": "Jupiter"},
    {"question": "What year did the Titanic sink?", "answer": "1912"}
]

def register_with_naming_server():
    try:
        response = request_response(
            NAMING_HOST, NAMING_PORT,
            {
                "type": "register",
                "service": "trivia.server.main",
                "host": "127.0.0.1",
                "port": PORT
            }
        )
        print(f"[SERVER] Registered with naming server: {response}")
    except Exception as e:
        print(f"[SERVER] Failed to register with Naming Server: {e}")

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
        print(f"[SERVER] {player_id} answered: {ans}")

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
    global round_active, current_winner
    question_number = 1
    
    print("[SERVER] Waiting 5 seconds for players to connect...")
    time.sleep(5)

    clients.broadcast({"type": "START", "payload": {"message": "Trivia game starting!"}})
    time.sleep(2)

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
        
        timeout = 10
        start = time.time()
        
        while time.time() - start < timeout:
            if not round_active: break
            time.sleep(0.1)

        if round_active:
            round_active = False
            clients.broadcast({"type": "RESULT", "payload": {"message": "No one buzzed in.", "lamport_time": clock.increment()}})
            print("[SERVER] No buzz received.")

        time.sleep(8) 
        question_number += 1

    clients.broadcast({"type": "END", "payload": {"message": "Game Over"}})
    print("\n[SERVER] Game finished.")

if __name__ == "__main__":
    print("[SERVER] Starting Trivia Host Server...")
    register_with_naming_server()
    threading.Thread(target=start_tcp_server, daemon=True).start()
    game_loop()