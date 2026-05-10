import socket
import threading
import json

print("Starting Naming Server...")

HOST = "0.0.0.0"
PORT = 4000

# service_name -> (ip, port)
registry = {}

lock = threading.Lock()


def handle_client(conn, addr):
    try:
        while True:
            data = conn.recv(1024).decode().strip()
            if not data:
                break

            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            # -------------------------
            # REGISTER SERVICE
            # -------------------------
            if msg_type == "register":
                service = msg.get("service")
                host = msg.get("host")
                port = msg.get("port")

                if service and host and port:
                    with lock:
                        registry[service] = (host, port)

                    print(f"[REGISTER] {service} -> {host}:{port}")

                    response = {"status": "ok"}

                else:
                    response = {"status": "error", "message": "invalid register data"}

                conn.send((json.dumps(response) + "\n").encode())

            # -------------------------
            # RESOLVE SERVICE
            # -------------------------
            elif msg_type == "resolve":
                service = msg.get("service")

                with lock:
                    result = registry.get(service)

                if result:
                    host, port = result
                    response = {
                        "status": "ok",
                        "host": host,
                        "port": port
                    }
                else:
                    response = {
                        "status": "error",
                        "message": "service not found"
                    }

                conn.send((json.dumps(response) + "\n").encode())

    except Exception as e:
        print(f"[ERROR] {e}")

    finally:
        conn.close()


def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen()

    print(f"[STARTED] Naming Server running on {HOST}:{PORT}")

    while True:
        conn, addr = server.accept()
        thread = threading.Thread(target=handle_client, args=(conn, addr))
        thread.start()


if __name__ == "__main__":
    start_server()