"""Experimental handshake candidate detector, disabled until hardware validation.

Input acceleration is in g and t is device milliseconds. A candidate requires
repeated directional reversals, not simply a large acceleration. Synthetic replay
checks logic only; ordinary arm movements can still resemble handshakes.
"""
from collections import deque
import math


class HandshakeDetector:
    def __init__(self, cooldown=4.0, window=1.2, threshold=0.35):
        self.cooldown = cooldown
        self.window = window
        self.threshold = threshold
        self.samples = deque()
        self.last_trigger = float('-inf')
        self.last_time = None

    def feed(self, sample):
        get = sample.get if isinstance(sample, dict) else lambda key: getattr(sample, key, None)
        try:
            t = float(get('t')) / 1000
            xyz = tuple(float(get(k)) for k in ('ax', 'ay', 'az'))
        except (TypeError, ValueError):
            return False
        if not all(math.isfinite(v) for v in (t, *xyz)):
            return False
        if self.last_time is not None and t <= self.last_time:
            self.samples.clear()
            self.last_time = t
            return False
        self.last_time = t
        self.samples.append((t, xyz))
        while self.samples and t - self.samples[0][0] > self.window:
            self.samples.popleft()
        if t - self.last_trigger < self.cooldown or len(self.samples) < 12:
            return False
        duration = t - self.samples[0][0]
        if duration < 0.5:
            return False
        for axis in range(3):
            values = [xyz[axis] for _, xyz in self.samples]
            center = sum(values) / len(values)
            signs = [1 if v-center > self.threshold else -1 if v-center < -self.threshold else 0 for v in values]
            signs = [s for s in signs if s]
            reversals = sum(a != b for a, b in zip(signs, signs[1:]))
            if 4 <= reversals <= 12 and max(values)-min(values) <= 4:
                self.last_trigger = t
                self.samples.clear()
                return True
        return False
