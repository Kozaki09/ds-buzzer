import socket
import threading
import json
import time

print("Starting Naming Server...")

HOST = "0.0.0.0"
PORT = 4000

registry = {}
lock = threading.Lock()

def ttl_monitor():
    """Constantly scans for and removes dead services."""
    while True:
        current_time = time.time()
        dead_services = []
        
        with lock:
            for service, data in registry.items():
                # If a service hasn't pinged in 15 seconds, it's dead
                if current_time - data["last_seen"] > 15:
                    dead_services.append(service)
            
            for dead in dead_services:
                print(f"[DNS] Service '{dead}' expired (TTL exceeded). Removing.")
                del registry[dead]
                
        time.sleep(5)

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

def discovery_responder():
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_sock.bind(("0.0.0.0", 4001)) 
    
    print(f"[DISCOVERY] Listening for LAN discovery on UDP 4001")
    while True:
        try:
            data, addr = udp_sock.recvfrom(1024)
            if data.decode() == "WHERE_IS_NAMING_SERVER":
                response = json.dumps({"host": get_local_ip(), "port": 4000})
                udp_sock.sendto(response.encode(), addr)
        except Exception as e:
            pass


def handle_client(conn, addr):
    conn.settimeout(5.0)
    try:
        while True:
            # Receive data
            raw_data = conn.recv(1024)
            if not raw_data:
                break

            data = raw_data.decode('utf-8').strip()
            if not data:
                continue

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
                        # NEW: Save the current timestamp
                        registry[service] = {
                            "host": host, 
                            "port": port, 
                            "last_seen": time.time()
                        }

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
                    # NEW: Unpack from the dictionary
                    response = {
                        "status": "ok",
                        "host": result["host"],
                        "port": result["port"]
                    }
                else:
                    response = {
                        "status": "error",
                        "message": "service not found or expired"
                    }

                conn.send((json.dumps(response) + "\n").encode())

    except (ConnectionResetError, BrokenPipeError):
        # This handles the [WinError 10054] gracefully on the server side
        print(f"[DISCONNECT] {addr} closed the connection abruptly.")
    except Exception as e:
        print(f"[ERROR] {addr}: {e}")
    finally:
        conn.close()


def start_server():
    # Force the discovery responder to start
    t = threading.Thread(target=discovery_responder, daemon=True)
    t.start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR is critical for restarting after crashes on Linux
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        server.bind((HOST, PORT))
        server.listen(128) # Explicitly set a larger backlog queue
        print(f"[STARTED] Naming Server running on {HOST}:{PORT}")
    except Exception as e:
        print(f"[CRITICAL] Could not bind to port {PORT}: {e}")
        return

    while True:
        # If nothing prints here when the client connects, 
        # the Pi's firewall or another app is intercepting the packet.
        conn, addr = server.accept()
        print(f"[NEW CONNECTION] Accepted from {addr}") 
        thread = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
        thread.start()

if __name__ == "__main__":
    start_server()