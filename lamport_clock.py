class LamportClock:
    def __init__(self):
        self.clock = 0

    def increment(self):
        self.clock += 1
        return self.clock

    def update(self, received_timestamp):
        if not isinstance(received_timestamp, int):
            raise ValueError("Timestamp must be an integer.")

        self.clock = max(self.clock, received_timestamp) + 1
        return self.clock

    def get_time(self):
        return self.clock

    def reset(self):
        self.clock = 0