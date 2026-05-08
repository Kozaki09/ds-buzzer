import socket, threading, json, time

HOST = "0.0.0.0"
PORT = 5000

clients = []
clients_lock = threading.Lock()

highest_bid = 0
highest_bidder = None

auction_active = True

lamport_clock = 0
clock_lock = threading.Lock()


# =========================
# Lamport Clock Functions
# =========================

def increment_clock():
    global lamport_clock

    with clock_lock:
        lamport_clock += 1
        return lamport_clock


def update_clock(received_timestamp):
    global lamport_clock

    with clock_lock:
        lamport_clock = max(lamport_clock, received_timestamp) + 1
        return lamport_clock


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
# Broadcast
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

            if data["type"] == "join":
                print(f"{data['username']} joined")

            elif data["type"] == "bid":

                sender_time = data["timestamp"]

                current_clock = update_clock(sender_time)

                username = data["username"]
                amount = data["amount"]

                print(
                    f"[BID] {username} bid ${amount} "
                    f"(client ts={sender_time}, server ts={current_clock})"
                )

                # Auction logic
                if amount > highest_bid:

                    highest_bid = amount
                    highest_bidder = username

                    increment_clock()

                    broadcast({
                        "type": "broadcast",
                        "message": f"{username} is now highest bidder",
                        "highest_bid": highest_bid,
                        "highest_bidder": highest_bidder,
                        "timestamp": lamport_clock
                    })

    except Exception as e:
        print(f"[ERROR] {e}")

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

    time.sleep(60)

    auction_active = False

    print("\n=== AUCTION ENDED ===")

    result = {
        "type": "result",
        "winner": highest_bidder,
        "amount": highest_bid
    }

    broadcast(result)


# =========================
# Main Server
# =========================

def main():

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    server.bind((HOST, PORT))
    server.listen()

    print(f"[SERVER STARTED] {HOST}:{PORT}")

    # Start auction timer
    threading.Thread(target=auction_timer, daemon=True).start()

    while auction_active:

        try:
            client_socket, address = server.accept()

            with clients_lock:
                clients.append(client_socket)

            thread = threading.Thread(
                target=handle_client,
                args=(client_socket, address),
                daemon=True
            )

            thread.start()

        except KeyboardInterrupt:
            break

    server.close()


if __name__ == "__main__":
    main()