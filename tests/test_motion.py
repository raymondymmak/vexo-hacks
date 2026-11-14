import math
import unittest
from remember.motion import HandshakeDetector
from remember.splitter import Splitter


def replay(detector, fn, start=0, seconds=2):
    return [detector.feed({'t': (start+i/50)*1000, 'ax': fn(i/50), 'ay': 0, 'az': 1})
            for i in range(int(seconds*50))]


class MotionTests(unittest.TestCase):
    def test_stationary_and_slow_motion_do_not_trigger(self):
        self.assertFalse(any(replay(HandshakeDetector(), lambda t: 0)))
        self.assertFalse(any(replay(HandshakeDetector(), lambda t: math.sin(t))))

    def test_repeated_shake_triggers_once_during_cooldown(self):
        detector = HandshakeDetector()
        self.assertEqual(sum(replay(detector, lambda t: .8*math.sin(2*math.pi*4*t))), 1)
        self.assertTrue(any(replay(detector, lambda t: .8*math.sin(2*math.pi*4*t), start=6)))

    def test_invalid_sample_is_ignored(self):
        detector = HandshakeDetector()
        self.assertFalse(detector.feed({'t': 1, 'ax': float('nan'), 'ay': 0, 'az': 1}))
        self.assertFalse(detector.feed({}))

    def test_start_does_not_split_and_double_tap_ends(self):
        splitter = Splitter()
        self.assertEqual(splitter.mark(), '')
        self.assertIsNone(splitter.open)
        splitter.start()
        splitter.hear('Hello', True)
        splitter.start()
        self.assertEqual(splitter.double_tap().transcript, 'Hello')
        self.assertIsNone(splitter.open)
        splitter.double_tap()
        self.assertIsNotNone(splitter.open)
        splitter.double_tap()
        self.assertIsNone(splitter.open)
