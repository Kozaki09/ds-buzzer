import socket
import threading
import json
import time
from lamport_clock import LamportClock

NAMING_HOST = "127.0.0.1"
NAMING_PORT = 4000
HOST = "0.0.0.0"
PORT = 5000
MULTICAST_GROUP = "224.0.0.1"
MULTICAST_PORT = 5007

clock = LamportClock()
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
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((NAMING_HOST, NAMING_PORT))
        msg = json.dumps({
            "type": "register",
            "service": "trivia.server.main",
            "host": "127.0.0.1",
            "port": PORT
        }) + "\n"
        sock.sendall(msg.encode())
        print(f"[SERVER] Registered with naming server: {sock.recv(1024).decode().strip()}")
        sock.close()
    except Exception as e:
        print(f"[SERVER] Failed to register with Naming Server: {e}")

def multicast_message(msg_dict):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.sendto(json.dumps(msg_dict).encode(), (MULTICAST_GROUP, MULTICAST_PORT))
    sock.close()

def handle_client(conn, addr):
    global round_active, current_winner
    try:
        data = conn.recv(1024).decode()
        if not data: return
        msg = json.loads(data)
        
        if msg.get("type") == "BUZZ":
            payload = msg.get("payload", {})
            player_id = payload.get("player_id")
            client_time = payload.get("lamport_time", 0)
            
            clock.update(client_time)
            
            won = False
            # 1. Lock ONLY to check/set the winner
            with buzz_lock:
                if round_active:
                    round_active = False
                    current_winner = player_id
                    won = True

            # 2. OUTSIDE the lock: handle the network communication
            if won:
                print(f"[SERVER] {player_id} buzzed in first!")
                response = {
                    "type": "WINNER",
                    "payload": {"you_won": True, "lamport_time": clock.increment()}
                }
                conn.sendall((json.dumps(response) + "\n").encode())
                
                multicast_message({
                    "type": "WINNER",
                    "payload": {"winner": player_id, "lamport_time": clock.increment()}
                })
                
                # Wait for the answer (Now it won't block the next round!)
                ans_data = conn.recv(1024).decode()
                if ans_data:
                    ans_msg = json.loads(ans_data)
                    print(f"[SERVER] {player_id} answered: {ans_msg['payload']['answer']}")
            else:
                response = {
                    "type": "WINNER",
                    "payload": {"you_won": False, "winner": current_winner, "lamport_time": clock.increment()}
                }
                conn.sendall((json.dumps(response) + "\n").encode())
    except Exception as e:
        print(f"[SERVER] Client error: {e}")
    finally:
        conn.close()

def start_tcp_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen()
    print(f"[SERVER] TCP Listening on {HOST}:{PORT} for buzzes...")
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

def game_loop():
    global round_active, current_winner
    question_number = 1
    
    multicast_message({"type": "START", "payload": {"message": "Trivia game starting!"}})
    time.sleep(2)

    for q in questions:
        print(f"\n[SERVER] Sending Question {question_number}...")
        clock.increment()
        
        multicast_message({
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
            multicast_message({"type": "RESULT", "payload": {"message": "No one buzzed in.", "lamport_time": clock.increment()}})
            print("[SERVER] No buzz received.")

        time.sleep(3) # Wait before next question
        question_number += 1

    multicast_message({"type": "END", "payload": {"message": "Game Over"}})
    print("\n[SERVER] Game finished.")

if __name__ == "__main__":
    print("[SERVER] Starting Trivia Host Server...")
    register_with_naming_server()
    threading.Thread(target=start_tcp_server, daemon=True).start()
    game_loop()