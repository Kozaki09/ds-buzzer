# Lamport Logical Clock Module

## Overview

This module implements Lamport Logical Clocks for synchronization in a distributed system project.

The Lamport Clock ensures fair event ordering even when network delays occur.

This module is used by:
- auction_server.py
- bidder_client.py

---

## File

```text
lamport_clock.py
```

---

## Features

- Local event clock increment
- Send event timestamping
- Receive event synchronization
- Logical time tracking
- Timestamp validation
- Reset functionality

---

## Lamport Clock Rules

### Rule 1 — Local Event

Whenever a process performs a local event:

```python
clock += 1
```

Examples:
- User places a bid
- Server processes a request

---

### Rule 2 — Sending a Message

Before sending a message:

```python
clock += 1
send(timestamp=clock)
```

---

### Rule 3 — Receiving a Message

When receiving a message:

```python
clock = max(local_clock, received_timestamp) + 1
```

---

## Class Structure

```python
class LamportClock:
```

### Methods

| Method | Description |
|---|---|
| increment() | Increases clock for local/send events |
| update(timestamp) | Synchronizes clock on receive |
| get_time() | Returns current clock value |
| reset() | Resets clock to 0 |

---

## Example Usage

```python
from lamport_clock import LamportClock

clock = LamportClock()

# Local event
timestamp = clock.increment()

print(timestamp)
```

---

## Example Receive Event

```python
clock.update(5)

print(clock.get_time())
```

---

## Fair Ordering Logic

The auction server sorts bids using:

```python
(timestamp, username)
```

Example:

| Player | Physical Arrival | Lamport Timestamp |
|---|---|---|
| Bob | First | 8 |
| Alice | Second | 5 |

Even if Bob's message arrives first physically,
Alice logically acted first.

Therefore:
- Alice wins

---

## Technologies Used

- Python 3
- TCP Sockets
- JSON
- Threading

---

## Author

CmpSc 160 — Distributed Systems Final Project