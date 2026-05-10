"""
utils.py

Member 2: Middleware / Communications Layer

This file provides reusable communication functions for the Distributed
Trivia Buzzer / Auction system.

Features:
- TCP socket creation
- JSON message sending
- JSON message receiving
- Newline-delimited JSON protocol
- Thread-safe sending
- Broadcast / multicast support
- Receiver thread helper for asynchronous communication

All messages are sent as:

    JSON_OBJECT + "\n"

Example:
    {"type": "bid", "username": "Alice", "amount": 100, "timestamp": 5}\n

The newline is important because TCP is a stream protocol. Without a delimiter,
multiple JSON messages may arrive merged together, or one JSON message may arrive
in pieces.
"""

from __future__ import annotations

import json
import socket
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple


Message = Dict[str, Any]
Address = Tuple[str, int]

ENCODING = "utf-8"
MAX_MESSAGE_BYTES = 64 * 1024


class NetworkError(Exception):
    """Raised when a socket/network operation fails."""

    pass


class MessageFormatError(Exception):
    """Raised when an invalid JSON message is received."""

    pass


def encode_message(message: Message) -> bytes:
    """
    Convert a Python dictionary into newline-delimited JSON bytes.

    Parameters:
        message: Dictionary to send over the network.

    Returns:
        Encoded bytes ending in newline.
    """
    if not isinstance(message, dict):
        raise MessageFormatError("Outgoing message must be a dictionary.")

    try:
        json_text = json.dumps(message, separators=(",", ":"), ensure_ascii=True)
    except TypeError as exc:
        raise MessageFormatError(f"Message is not JSON serializable: {exc}") from exc

    payload = (json_text + "\n").encode(ENCODING)

    if len(payload) > MAX_MESSAGE_BYTES:
        raise MessageFormatError("Outgoing message is too large.")

    return payload


def decode_message(line: str) -> Message:
    """
    Convert one JSON line into a Python dictionary.

    Parameters:
        line: One line of JSON text.

    Returns:
        Python dictionary.
    """
    line = line.strip()

    if not line:
        raise MessageFormatError("Received empty message.")

    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MessageFormatError(f"Invalid JSON received: {exc}") from exc

    if not isinstance(message, dict):
        raise MessageFormatError("Incoming JSON message must be an object/dictionary.")

    return message


class JsonConnection:
    """
    Wrapper around a TCP socket that sends and receives newline-delimited JSON.

    This class is used by:
    - naming_server.py
    - auction_server.py
    - bidder_client.py

    Each connection has its own send lock so multiple threads can safely send
    through the same socket.
    """

    def __init__(
        self,
        sock: socket.socket,
        address: Optional[Address] = None,
        label: Optional[str] = None,
    ):
        self.sock = sock
        self.address = address
        self.label = label or self._make_label(address)
        self.closed = False

        self._send_lock = threading.Lock()

        # Text reader for line-based JSON receiving.
        self._reader = self.sock.makefile("r", encoding=ENCODING, newline="\n")

    def _make_label(self, address: Optional[Address]) -> str:
        if address is None:
            return "unknown"
        return f"{address[0]}:{address[1]}"

    def send(self, message: Message) -> None:
        """
        Send one JSON message.

        This method is thread-safe.
        """
        if self.closed:
            raise NetworkError(f"Cannot send; connection {self.label} is closed.")

        payload = encode_message(message)

        try:
            with self._send_lock:
                self.sock.sendall(payload)
        except OSError as exc:
            self.closed = True
            raise NetworkError(f"Failed to send to {self.label}: {exc}") from exc

    def recv(self) -> Optional[Message]:
        """
        Receive one JSON message.

        Returns:
            dict if a message is received.
            None if the other side closed the connection.
        """
        if self.closed:
            return None

        try:
            line = self._reader.readline(MAX_MESSAGE_BYTES + 1)
        except OSError as exc:
            self.closed = True
            raise NetworkError(f"Failed to receive from {self.label}: {exc}") from exc

        # Empty string means the remote side closed the connection.
        if line == "":
            self.closed = True
            return None

        if len(line.encode(ENCODING)) > MAX_MESSAGE_BYTES:
            raise MessageFormatError(f"Message from {self.label} is too large.")

        if not line.endswith("\n"):
            raise MessageFormatError(f"Incomplete JSON message from {self.label}.")

        return decode_message(line)

    def close(self) -> None:
        """
        Close the connection safely.
        """
        if self.closed:
            return

        self.closed = True

        try:
            self._reader.close()
        except Exception:
            pass

        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        try:
            self.sock.close()
        except OSError:
            pass

    def __repr__(self) -> str:
        return f"JsonConnection(label={self.label})"


def create_server_socket(host: str, port: int, backlog: int = 20) -> socket.socket:
    """
    Create a TCP server socket.

    Example:
        server_sock = create_server_socket("0.0.0.0", 5000)
    """
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Allows quick restart after crash without waiting for old socket timeout.
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server_sock.bind((host, port))
    server_sock.listen(backlog)

    return server_sock


def accept_json_connection(server_sock: socket.socket) -> JsonConnection:
    """
    Accept a new TCP client and wrap it as a JsonConnection.

    Used by the Auction Server and Naming Server.
    """
    client_sock, address = server_sock.accept()
    return JsonConnection(client_sock, address=address)


def connect_to_server(
    host: str,
    port: int,
    connect_timeout: float = 5.0,
    read_timeout: Optional[float] = None,
    label: Optional[str] = None,
) -> JsonConnection:
    """
    Connect to a TCP server and return a JsonConnection.

    Used by:
    - Auction server connecting to Naming server
    - Bidder clients connecting to Naming server
    - Bidder clients connecting to Auction server
    """
    try:
        sock = socket.create_connection((host, port), timeout=connect_timeout)

        # After connection, choose whether reads block forever or timeout.
        # For normal bidder/auction communication, use read_timeout=None.
        # For quick request-response naming calls, use read_timeout=5.0.
        sock.settimeout(read_timeout)

        return JsonConnection(sock, address=(host, port), label=label)

    except OSError as exc:
        raise NetworkError(f"Could not connect to {host}:{port}: {exc}") from exc


def request_response(
    host: str,
    port: int,
    request: Message,
    connect_timeout: float = 5.0,
    response_timeout: float = 5.0,
) -> Message:
    """
    Send one request and wait for one response.

    Useful for communication with the Naming Server.

    Example:
        response = request_response(
            "127.0.0.1",
            4000,
            {"type": "resolve", "service": "auction.server.main"}
        )
    """
    conn = connect_to_server(
        host, port, connect_timeout=connect_timeout, read_timeout=response_timeout
    )

    try:
        conn.send(request)
        response = conn.recv()

        if response is None:
            raise NetworkError("Server closed connection before sending response.")

        return response

    finally:
        conn.close()


class ConnectionManager:
    """
    Thread-safe collection of active JsonConnection objects.

    The Auction Server should use this to keep track of all connected bidders.

    Example:
        clients = ConnectionManager()
        clients.add(conn)
        clients.broadcast({"type": "broadcast", "message": "New highest bid"})
    """

    def __init__(self):
        self._connections: List[JsonConnection] = []
        self._lock = threading.RLock()

    def add(self, conn: JsonConnection) -> None:
        with self._lock:
            if conn not in self._connections:
                self._connections.append(conn)

    def remove(self, conn: JsonConnection) -> None:
        with self._lock:
            if conn in self._connections:
                self._connections.remove(conn)

        conn.close()

    def all(self) -> List[JsonConnection]:
        """
        Return a snapshot copy of all connections.
        """
        with self._lock:
            return list(self._connections)

    def count(self) -> int:
        with self._lock:
            return len(self._connections)

    def broadcast(
        self, message: Message, exclude: Optional[JsonConnection] = None
    ) -> List[JsonConnection]:
        """
        Send a message to all connected clients.

        Parameters:
            message: Message to broadcast.
            exclude: Optional connection to skip.

        Returns:
            List of failed/dead connections.
        """
        failed_connections: List[JsonConnection] = []

        with self._lock:
            connections_snapshot = list(self._connections)

        for conn in connections_snapshot:
            if exclude is not None and conn is exclude:
                continue

            try:
                conn.send(message)
            except NetworkError:
                failed_connections.append(conn)

        # Remove dead connections after broadcasting.
        for dead_conn in failed_connections:
            self.remove(dead_conn)

        return failed_connections

    def close_all(self) -> None:
        with self._lock:
            connections_snapshot = list(self._connections)
            self._connections.clear()

        for conn in connections_snapshot:
            conn.close()


OnMessage = Callable[[JsonConnection, Message], None]
OnDisconnect = Callable[[JsonConnection, Optional[BaseException]], None]


def start_receiver_thread(
    conn: JsonConnection,
    on_message: OnMessage,
    on_disconnect: Optional[OnDisconnect] = None,
    thread_name: Optional[str] = None,
    daemon: bool = True,
) -> threading.Thread:
    """
    Start a background thread that continuously receives messages.

    Used by:
    - Auction server for each bidder client
    - Bidder client for receiving auction broadcasts

    Parameters:
        conn: JsonConnection to read from.
        on_message: Function called every time a message arrives.
        on_disconnect: Optional function called when the connection closes.
        thread_name: Optional thread name.
        daemon: Whether the thread should stop automatically when program exits.

    Example:
        def handle_message(conn, msg):
            print("Received:", msg)

        start_receiver_thread(conn, handle_message)
    """

    def receiver_loop() -> None:
        error: Optional[BaseException] = None

        try:
            while True:
                message = conn.recv()

                if message is None:
                    break

                on_message(conn, message)

        except BaseException as exc:
            error = exc

        finally:
            conn.close()

            if on_disconnect is not None:
                on_disconnect(conn, error)

    thread = threading.Thread(
        target=receiver_loop,
        name=thread_name or f"Receiver-{conn.label}",
        daemon=daemon,
    )

    thread.start()
    return thread
