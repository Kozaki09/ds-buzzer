import socket
import threading
import json
import time

from lamport_clock import LamportClock


HOST = "0.0.0.0"
PORT = 5000

AUCTION_DURATION = 60

clients = []
clients_lock = threading.Lock()

highest_bid = 0
highest_bidder = None

auction_active = True

# Lamport Clock Instance
clock = LamportClock()

# =========================
# Naming Server Config
# =========================

NAMING_SERVER_HOST = "127.0.0.1"
NAMING_SERVER_PORT = 4000

SERVICE_NAME = "auction.server.main"


# =========================
# Socket Helpers
# =========================

def send_json(sock, data):
    message = json.dumps(data) + "\n"
    sock.sendall(message.encode())


def recv_json(sock):

    buffer = ""

    while "\n" not in buffer:

        data = sock.recv(1024).decode()

        if not data:
            return None

        buffer += data

    line, _ = buffer.split("\n", 1)

    return json.loads(line)


# =========================
# Broadcast Function
# =========================

def broadcast(message_data):

    dead_clients = []

    with clients_lock:

        for client in clients:

            try:
                send_json(client, message_data)

            except:
                dead_clients.append(client)

        for dead in dead_clients:
            clients.remove(dead)


# =========================
# Client Handler
# =========================

def handle_client(client_socket, address):

    global highest_bid
    global highest_bidder

    print(f"[CONNECTED] {address}")

    try:

        while auction_active:

            data = recv_json(client_socket)

            if data is None:
                break

            message_type = data.get("type")

            # =========================
            # JOIN EVENT
            # =========================

            if message_type == "join":

                username = data.get("username")

                clock.increment()

                print(
                    f"[JOIN] {username} joined "
                    f"(server clock={clock.get_time()})"
                )

            # =========================
            # BID EVENT
            # =========================

            elif message_type == "bid":

                username = data.get("username")
                amount = data.get("amount")
                received_timestamp = data.get("timestamp")

                # Lamport receive rule
                updated_time = clock.update(received_timestamp)

                print(
                    f"[BID RECEIVED] "
                    f"{username} bid ${amount} "
                    f"(client ts={received_timestamp}, "
                    f"server ts={updated_time})"
                )

                # Auction Logic
                if amount > highest_bid:

                    highest_bid = amount
                    highest_bidder = username

                    # Local event after processing
                    current_time = clock.increment()

                    broadcast_message = {
                        "type": "broadcast",
                        "message": (
                            f"{username} is now highest bidder "
                            f"with ${amount}"
                        ),
                        "highest_bid": highest_bid,
                        "highest_bidder": highest_bidder,
                        "timestamp": current_time
                    }

                    print(
                        f"[BROADCAST] "
                        f"Highest bid updated to ${highest_bid}"
                    )

                    broadcast(broadcast_message)

                else:

                    rejection_message = {
                        "type": "rejected",
                        "message": (
                            f"Bid rejected. "
                            f"Current highest bid is ${highest_bid}"
                        ),
                        "timestamp": clock.increment()
                    }

                    send_json(client_socket, rejection_message)

    except Exception as e:

        print(f"[ERROR] {address}: {e}")

    finally:

        print(f"[DISCONNECTED] {address}")

        with clients_lock:
            if client_socket in clients:
                clients.remove(client_socket)

        client_socket.close()


# =========================
# Auction Timer
# =========================

def auction_timer():

    global auction_active

    print(f"[AUCTION STARTED] Duration: {AUCTION_DURATION} seconds")

    time.sleep(AUCTION_DURATION)

    auction_active = False

    final_timestamp = clock.increment()

    result_message = {
        "type": "result",
        "winner": highest_bidder,
        "amount": highest_bid,
        "timestamp": final_timestamp
    }

    print("\n=== AUCTION ENDED ===")

    if highest_bidder:
        print(
            f"Winner: {highest_bidder} "
            f"with ${highest_bid}"
        )
    else:
        print("No bids received.")

    broadcast(result_message)


# =========================
# Register With Naming Server
# =========================

def register_with_naming_server():

    try:

        naming_socket = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        )

        naming_socket.connect(
            (NAMING_SERVER_HOST, NAMING_SERVER_PORT)
        )

        registration_message = {
            "type": "register",
            "service": SERVICE_NAME,
            "host": "127.0.0.1",
            "port": PORT
        }

        message = json.dumps(registration_message) + "\n"

        naming_socket.sendall(message.encode())

        response = naming_socket.recv(1024).decode()

        print(f"[NAMING SERVER RESPONSE] {response.strip()}")

        naming_socket.close()

    except Exception as e:

        print(f"[NAMING SERVER ERROR] {e}")


# =========================
# Main Server
# =========================

def main():

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    server.bind((HOST, PORT))
    server.listen()
    server.settimeout(1)

    print(f"[SERVER STARTED] Listening on {HOST}:{PORT}")

    register_with_naming_server()

    # Start Auction Countdown
    timer_thread = threading.Thread(
        target=auction_timer
    )

    timer_thread.start()

    while auction_active:
        try:
            client_socket, address = server.accept()

            with clients_lock:
                clients.append(client_socket)

            client_thread = threading.Thread(
                target=handle_client,
                args=(client_socket, address)
            )

            client_thread.start()

        except KeyboardInterrupt:
            print("\n[SERVER SHUTDOWN]")
            break
        
        except socket.timeout:
            continue

    server.close()


if __name__ == "__main__":
    main()