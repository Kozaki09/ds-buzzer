import sys
import os
import threading
from lamport_clock import LamportClock
from utils import connect_to_server, request_response, start_receiver_thread

NAMING_HOST = "127.0.0.1"
NAMING_PORT = 4000

def wait_for_keypress():
    '''Block until the player presses any key (Cross-Platform).'''
    if os.name == 'nt':
        import msvcrt
        msvcrt.getch()
    else:
        import tty, termios
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
        self.conn = None

        self.current_question = None
        self.current_question_number = None
        
        self.question_lock = threading.Lock()
        self.buzzed_this_round = False
        
        # State flag to stop keyboard input collision when typing an answer
        self.question_lock = threading.Lock()
        self.buzzed_this_round = False
        
        # NEW: Thread synchronization for the buzzer
        self.buzz_response_event = threading.Event()
        self.won_buzz = False

        print(f"[Client] Starting as player: {username}")

        print(f"[Client] Starting as player: {username}")

    def register(self):
        try:
            response = request_response(
                NAMING_HOST, NAMING_PORT,
                {"type": "register", "service": self.username, "host": "127.0.0.1", "port": 9999}
            )
            if response.get("status") == "ok":
                print(f"[Client] Registered as {self.username}")
            else:
                print(f"[Client] Registration failed: {response}")
        except Exception as e:
            print(f"[Client] Naming server error: {e}")

    def resolve_server(self):
        response = request_response(
            NAMING_HOST, NAMING_PORT,
            {"type": "resolve", "service": "trivia.server.main"}
        )
        if response.get("status") != "ok":
            raise Exception("Could not resolve server")
        return response["host"], response["port"]

    def handle_server_message(self, conn, msg):
        """Callback for start_receiver_thread when the server sends a broadcast"""
        msg_type = msg.get("type")

        if msg_type == "START":
            print(f"\n[Client] {msg['payload']['message']}")

        elif msg_type == "QUESTION":
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
            print(f"Press ANY KEY to buzz in!")
            print(f"{'='*50}")

        elif msg_type == "WINNER": 
            payload = msg.get("payload", {})
            # Check if this is a direct response to OUR buzz, or a broadcast to everyone
            is_direct_response = "you_won" in payload
            you_won = payload.get("you_won", False)
            ts = payload.get("lamport_time", "?")
            winner = payload.get("winner", "someone else")

            if str(ts).isdigit():
                self.clock.update(int(ts))

            if you_won:
                print(f"\n[Client] YOU WON the buzz! (Lamport: {ts})")
                print(f"Question: {self.current_question}")
                self.won_buzz = True
                self.buzz_response_event.set() # Wake up the main thread
                
            elif is_direct_response:
                print(f"\n[Client] You were too late. '{winner}' buzzed in first.")
                self.won_buzz = False
                self.buzz_response_event.set() # Wake up the main thread
                
            else:
                print(f"\n[Client] '{winner}' buzzed in first (Lamport: {ts}). Better luck next time!")
                self.buzzed_this_round = True # Lock us out from buzzing
                
        elif msg_type == "RESULT":
            print(f"\n[Client] {msg['payload']['message']}")

        elif msg_type == "END":
            print(f"\n[Client] Game over! Thanks for playing.")
            os._exit(0)

    def handle_disconnect(self, conn, error):
        print("\n[Client] Lost connection to the server.")
        os._exit(1)

    def run(self):
        # 1. Register and connect
        self.register()
        host, port = self.resolve_server()
        
        self.conn = connect_to_server(host, port)
        
        # Announce presence to server
        self.conn.send({"type": "JOIN", "payload": {"player_id": self.username}})

        # 2. Start listening to server broadcasts in the background
        start_receiver_thread(self.conn, self.handle_server_message, self.handle_disconnect)

        print("[Client] Waiting for game to start...\n")

        # 3. Main thread handles keyboard buzzing exclusively
        while True: 
            try: 
                wait_for_keypress()

                with self.question_lock:
                    if self.buzzed_this_round or self.current_question is None:
                        continue
                    self.buzzed_this_round = True
                
                timestamp = self.clock.increment()
                print(f"[Client] Buzz! (Lamport: {timestamp})")

                # Clear the event and send the buzz
                self.buzz_response_event.clear()
                self.conn.send({
                    "type": "BUZZ",
                    "payload": {
                        "player_id": self.username,
                        "lamport_time": timestamp,
                        "question_number": self.current_question_number
                    }
                })

                # Pause the main thread until the server replies to our buzz
                self.buzz_response_event.wait(timeout=5.0)

                # If the background thread told us we won, ask for the answer safely!
                if self.won_buzz:
                    answer = input("Your answer: ").strip()
                    self.conn.send({
                        "type": "ANSWER", 
                        "payload": {
                            "player_id": self.username,
                            "answer": answer,
                            "lamport_time": self.clock.increment()
                        }
                    })
                    print("[Client] Answer submitted!")
                    self.won_buzz = False

            except KeyboardInterrupt:
                print("\n[Client] Exiting.")
                break

if __name__ == "__main__": 
    if len(sys.argv) < 2: 
        print("Usage: python trivia_client.py <your_username>")
        sys.exit(1)
    
    client = PlayerClient(sys.argv[1])
    client.run()