import sys
import os
import threading
import time
import socket
import json
from lamport_clock import LamportClock
from utils import connect_to_server, request_response, start_receiver_thread

# IMPORT PROTOCOL FACTORIES
from messages import MsgType, make_register_msg, make_resolve_msg, make_join, make_buzz, make_answer

def find_naming_server():
    """Hunts for the Naming Server using a staggered approach to avoid ARP flooding."""
    print("[NETWORK] Searching for Naming Server on LAN...")
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    if os.name == 'nt':
        SIO_UDP_CONNRESET = 0x9800000C
        try:
            udp_sock.ioctl(SIO_UDP_CONNRESET, False)
        except Exception:
            pass 

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        my_ip = s.getsockname()[0]
        s.close()
        subnet_prefix = ".".join(my_ip.split(".")[:-1]) + "."
    except Exception:
        subnet_prefix = "192.168.100." 

    while True:
        try:
            udp_sock.sendto(b"WHERE_IS_NAMING_SERVER", ("127.0.0.1", 4001))
            udp_sock.sendto(b"WHERE_IS_NAMING_SERVER", ("192.168.100.46", 4001))
            udp_sock.sendto(b"WHERE_IS_NAMING_SERVER", ("<broadcast>", 4001))
        except OSError:
            pass 

        udp_sock.settimeout(0.5)
        try:
            data, addr = udp_sock.recvfrom(1024)
            info = json.loads(data.decode())
            host, port = info["host"], info["port"]
            print(f"[NETWORK] Found Naming Server at {host}:{port}")
            return host, port
        except (socket.timeout, ConnectionResetError):
            pass 

        for i in range(1, 255):
            target_ip = f"{subnet_prefix}{i}"
            try:
                udp_sock.sendto(b"WHERE_IS_NAMING_SERVER", (target_ip, 4001))
            except OSError:
                pass 

        udp_sock.settimeout(1.0)
        try:
            data, addr = udp_sock.recvfrom(1024)
            info = json.loads(data.decode())
            host, port = info["host"], info["port"]
            print(f"[NETWORK] Found Naming Server at {host}:{port}")
            return host, port
        except (socket.timeout, ConnectionResetError):
            print("[NETWORK] Still searching...")

class PlayerClient:
    def __init__(self, username: str):
        self.username = username
        self.clock = LamportClock()
        self.conn = None

        self.current_question = None
        self.current_question_number = None
        
        self.question_lock = threading.Lock()
        self.buzzed_this_round = False
        
        self.buzz_response_event = threading.Event()
        self.won_buzz = False
        self.is_answering = False

        print(f"[Client] Starting as player: {username}")

    def connect_to_host(self):
        print("[Client] Looking for the Trivia Server...")
        while True:
            try:
                naming_host, naming_port = find_naming_server()
                
                request_response(
                    naming_host, naming_port,
                    make_register_msg(self.username, "127.0.0.1", 9999)
                )

                response = request_response(
                    naming_host, naming_port,
                    make_resolve_msg("trivia.server.main")
                )

                if response.get("status") != "ok":
                    raise Exception("Trivia server not registered yet.")

                host, port = response["host"], response["port"]
                self.conn = connect_to_server(host, port)
                
                self.conn.send(make_join(self.username))
                
                print("[Client] Successfully connected to the host!")
                break 

            except Exception as e:
                print(f"[Client] Host not ready ({e}). Retrying in 3 seconds...")
                time.sleep(3)

    def handle_server_message(self, conn, msg):
        msg_type = msg.get("type")

        if msg_type == MsgType.START:
            print(f"\n[Client] {msg['payload']['message']}")

        elif msg_type == MsgType.QUESTION:
            payload = msg.get("payload", {})
            q_num = msg.get("question_number")
            received_ts = payload.get("timestamp", 0)

            self.clock.update(received_ts)

            with self.question_lock: 
                self.current_question = payload.get("question")
                self.current_question_number = q_num
                self.buzzed_this_round = False
                self.is_answering = False
            
            print(f"\n{'='*50}")
            print(f"[Q{q_num}] {self.current_question}")
            print(f"Press ENTER to buzz in!")
            print(f"{'='*50}")

        elif msg_type == MsgType.WINNER: 
            payload = msg.get("payload", {})
            you_won = payload.get("you_won", False)
            ts = payload.get("lamport_time", "?")
            winner = payload.get("winner", "someone else")

            if str(ts).isdigit():
                self.clock.update(int(ts))

            if you_won:
                print(f"\n[Client] YOU WON the buzz! (Lamport: {ts})")
                print(f"Question: {self.current_question}")
                self.won_buzz = True
                self.buzz_response_event.set()
                
            else:
                if self.buzzed_this_round and not self.buzz_response_event.is_set():
                    print(f"\n[Client] You were too late. '{winner}' buzzed in first.")
                    self.won_buzz = False
                    self.buzz_response_event.set()
                else:
                    print(f"\n[Client] '{winner}' buzzed in first (Lamport: {ts}). Better luck next time!")
                    self.buzzed_this_round = True
                
        elif msg_type == MsgType.RESULT:
            print(f"\n[Client] {msg['payload']['message']}")

        elif msg_type == MsgType.END:
            print(f"\n[Client] Game over! Thanks for playing.")
            os._exit(0)

    def handle_disconnect(self, conn, error):
        print("\n[Client] Lost connection to the server.")
        os._exit(1)

    def run(self):
        self.connect_to_host()
        start_receiver_thread(self.conn, self.handle_server_message, self.handle_disconnect)

        print("[Client] Waiting for game to start...\n")

        while True: 
            try: 
                input() 
                
                if self.is_answering:
                    continue

                with self.question_lock:
                    if self.buzzed_this_round or self.current_question is None:
                        continue
                    self.buzzed_this_round = True
                
                timestamp = self.clock.increment()
                print(f"[Client] Buzz! (Lamport: {timestamp})")
                self.buzz_response_event.clear()

                self.conn.send(make_buzz(self.username, timestamp, self.current_question_number))
                self.buzz_response_event.wait(timeout=5.0)

                if self.won_buzz:
                    answer = input("Your answer: ").strip()
                    self.conn.send(make_answer(self.username, answer, self.clock.increment()))
                    print("[Client] Answer submitted!")
                    self.won_buzz = False

            except KeyboardInterrupt:
                print("\n[Client] Exiting.")
                break

if __name__ == "__main__": 
    if len(sys.argv) > 1:
        username = sys.argv[1]
    else:
        print("=== Distributed Trivia ===")
        username = ""
        while not username:
            username = input("Enter your username to join: ").strip()
    
    client = PlayerClient(username)
    client.run()