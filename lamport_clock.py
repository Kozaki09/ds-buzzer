import threading

class LamportClock:
    def __init__(self):
        self.time = 0
        self.lock = threading.Lock()

    def increment(self):
        with self.lock:
            self.time += 1
            return self.time

    def update(self, received_time):
        with self.lock:
            self.time = max(self.time, received_time) + 1
            return self.time

    def get_time(self):
        with self.lock:
            return self.time