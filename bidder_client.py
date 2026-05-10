import socket 
import threading
import json
import sys
import os

from lamport_clock import LamportClock

NAMING_HOST = "127.0.0.1"
NAMING_PORT = 4000

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5000

MULTICAST_GROUP = "224.0.0.1"
MULTICAST_PORT = 5007

BUFFER_SIZE = 4096

def send_json(sock, data):
    """Serialize dict to JSON and send over socket."""

    message = json.dumps(data) + "\n"
    sock.sendall(message.encode())


def recv_json(sock):
    """Receive a newline-terminated JSON message from socket"""
    buffer = ""
    while "\n" not in buffer:
        chunk = sock.recv(BUFFER_SIZE).decode()
        if not chunk:
            return None
        buffer += chunk
    line, _ = buffer.split("\n", 1)
    return json.loads(line)

def wait_for_keypress():
    '''Block until the player presses any key.'''
    import tty 
    import termios
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdin.read(1)
    finally: 
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


class PlayerClient:
    def __init__(self, username: str):
        self.username = username
        self.clock = LamportClock()

        self.current_question = None
        self.current_question_number = None
        self.question_lock = threading.Lock()

        self.buzzed_this_round = False

        print(f"[Client] Starting as player: {username}")
              
    def register(self):
        """Register this player with naming_server.py."""
        try: 
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((NAMING_HOST, NAMING_PORT))

            self.clock.increment()

            send_json(sock, {
                "type": "register",
                "service": self.username,
                "host": SERVER_HOST,
                "port": SERVER_PORT,
                "timestamp": self.clock.get_time()
            })

            response = recv_json(sock)
            sock.close()

            if response and response.get("status") == "ok":
                print(f"[Client] Registered with naming server as '{self.username}'")
            else: 
                print(f"[Client] Registration failed: {response}")

        except Exception as e: 
            print(f"[Client] Could not connect to naming server: {e}")
    
    def listen_for_questions(self): 
        """Runs in background thread. Joins the UDP multicast group and listens for questions from the host"""

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) 
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", MULTICAST_PORT))

        import struct
        group = socket.inet_aton(MULTICAST_GROUP)
        mreq = struct.pack("4sL", group, socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        print(f"[Client] Listening for questions on multicast {MULTICAST_GROUP}:{MULTICAST_PORT}")

        while True: 
            try:
                data, _ = sock.recvfrom(BUFFER_SIZE)
                msg = json.loads(data.decode())
                msg_type = msg.get("type")

                if msg_type == "QUESTION":
                    payload = msg.get("payload", {})
                    question = payload.get("question")
                    q_num = msg.get("question_number")

                    received_ts = payload.get("timestamp", 0)
                    self.clock.update(received_ts) if received_ts else self.clock.increment()

                    with self.question_lock: 
                        self.current_question = question
                        self.current_question_number = q_num
                        self.buzzed_this_round = False
                    
                    print(f"\n{'='*50}")
                    print(f"[Q{q_num}] {question}")
                    print(f"Press ANY KEY to buzz in!")
                    print(f"{'='*50}")

                elif msg_type == "WINNER": 
                    payload = msg.get("payload", {})
                    winner = payload.get("player_id") or payload.get("winner")
                    ts = payload.get("lamport_time", "?")

                    if winner == self.username:
                        print(f"\n[Client] YOU WON! (Lamport: {ts})")
                    else:
                        print(f"\n[Client]'{winner}' buzzed in first (Lamport:{ts}). Better luck next time!")

                elif msg_type == "START":
                    print(f"\n[Client] Game over! Thanks for playing.")
                    sys.exit(0)
            except Exception as e:
                print(f"[Client] Multicast error: {e}")
    
    def buzz_in(self):
        """Opens a TCP connection to the server and sends a BUZZ message"""

        with self.question_lock: 
            question = self.current_question
            q_num = self.current_question_number
        
        if not question or not q_num: 
            print("[Client] No active question to buzz in for.")
            return
        
        self.clock.increment()
        timestamp = self.clock.get_time()

        print(f"[Client] Buzz! (Lamport: {timestamp})")

        try: 
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((SERVER_HOST, SERVER_PORT))

            send_json(sock, {
                "type": "BUZZ",
                "payload": {
                    "player_id": self.username,
                    "lamport_time": timestamp,
                    "question_number": q_num
                }
            })

            sock.settimeout(10.0)
            response = recv_json(sock)

            if response is None:
                print("[Client] No response from server.")
                sock.close()
                return
            resp_type = response.get("type")
            payload = response.get("payload", {})

            resp_ts = payload.get("lamport_time", 0)
            if resp_ts:
                self.clock.update(resp_ts)

            if resp_type == "WINNER": 
                you_won = payload.get("you_won", False)

                if you_won: 
                    print(f"You buzzed in first! It's your turn to answer.")
                    print(f"Question: {question}")
                    answer = input("Your answer: ").strip()

                    send_json(sock, {
                        "type": "ANSWER", 
                        "payload": {
                            "player_id": self.username,
                            "answer": answer,
                            "lamport_time": self.clock.get_time()
                        }
                    })
                    print("[Client] Answer submitted!")
                
                else: 
                    winner = payload.get("winner", "someone else")
                    print(f"\n[Client] You were too slow. '{winner}' buzzed in first.")

            elif resp_type == "ACK": 
                info = payload.get("info", "")
                print(f"[Client] Server: {info}")
        
        except socket.timeout: 
            print("[Client] Server did not respond in time.")
        except Exception as e:
            print(f"[Client] Error during buzz: {e}")
        finally: 
            sock.close()

    def run(self):
        """Main entry point"""

        self.register()

        mc_thread = threading.Thread(target=self.listen_for_questions, daemon=True)
        mc_thread.start()

        print("[Client] Waiting for game to start. Press any key to buzz in when a question appears!\n")


        while True: 
            try: 
                wait_for_keypress()

                with self.question_lock:
                    already_buzzed = self.buzzed_this_round
                    if not already_buzzed:
                        self.buzzed_this_round = True
                
                if already_buzzed: 
                    print(f"[Client] You already buzzed in this round!")
                    continue

                buzz_thread = threading.Thread(target=self.buzz_in, daemon=True)
                buzz_thread.start()
            
            except KeyboardInterrupt:
                print("\n[Client] Exiting.")


if __name__ == "__main__": 
    if len(sys.argv) < 2: 
        print("Usage: python bidder_client.py <your_username>")
        sys.exit(1)
    
    username = sys.argv[1]
    client = PlayerClient(username)
    client.run()
