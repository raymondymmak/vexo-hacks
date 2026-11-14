"""Pretend band only. These tests never open Bluetooth."""

import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ["REMEMBER_STT"] = "mock"
os.environ["REMEMBER_EXTRACTOR"] = "builtin"

from remember.band import BandSession, flash_saved
from remember.extract import HeuristicExtractor
from remember.kitpath import ensure_kit
from remember.session import Session
from remember.voice import VoicePipeline

ensure_kit()
from vexo_open.sync import Band, VexoError
from vexo_open.transports.sim import SimTransport


def wait_until(predicate, timeout=4):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class BandTests(unittest.TestCase):
    def test_capture_is_off_until_start_and_stops_at_end(self):
        session = Session(":memory:", extractor=HeuristicExtractor())
        fake = MagicMock()
        fake.name = "Test band"
        fake.connected = True
        with patch("remember.band.connect_band", return_value=fake):
            bridge = BandSession(session, number=None, sim=True, listen=True)
            with patch.object(bridge, "_start_mic", return_value=True) as start_mic:
                bridge.start()
                start_mic.assert_not_called()
                self.assertFalse(session.state()["status"]["mic_on"])
                session.start()
                start_mic.assert_called_once()
                self.assertTrue(session.state()["status"]["mic_on"])
                session.end()
                fake.configure.assert_called_with(audio=False, mic_level=False)
                self.assertFalse(session.state()["status"]["mic_on"])
        session.close()

    def test_handshake_cannot_split_an_active_conversation(self):
        session = Session(":memory:", extractor=HeuristicExtractor())
        bridge = BandSession(session, number=None, sim=True, handshake=True)
        bridge.detector = MagicMock()
        bridge.detector.feed.return_value = True
        bridge._motion({})
        session.hear("A conversation", True)
        current = session.splitter.open
        bridge._motion({})
        self.assertIs(current, session.splitter.open)
        self.assertEqual(session.store.count(), 0)
        session.close()

    def test_green_waits_until_the_microphone_is_off(self):
        seen = []
        sim = SimTransport(auto_tap=False, on_led=lambda light: seen.append(dict(light)))
        band = Band.connect(None, transport=sim)
        try:
            band.configure(audio=True)
            with self.assertRaises(VexoError) as caught:
                band.set_led("#00ff00")
            self.assertEqual(caught.exception.code, "denied")
            self.assertTrue(flash_saved(band, seconds=0))
            self.assertIn("audio", list(band.streams))
            greens = [
                light for light in seen
                if light.get("source") == "user" and light.get("g", 0) >= 200 and light.get("r") == 0
            ]
            self.assertTrue(greens)
        finally:
            band.disconnect()

    def test_a_sim_tap_travels_through_the_pipeline(self):
        file = tempfile.NamedTemporaryFile(suffix=".sqlite")
        session = Session(file.name, extractor=HeuristicExtractor())
        pipeline = VoicePipeline(session, live=False, lines=[])
        band = None
        try:
            pipeline.start()
            band_session = BandSession(session, number=None, sim=True, pipeline=pipeline, listen=False)
            band = band_session
            band_session.start()
            band_session.band.transport.tap("double-tap")
            self.assertTrue(wait_until(lambda: session.state()["status"]["phase"] == "talking"))
            session.hear("Hi, I'm Maya Chen. I'm working on a soil sensor.", True)
            band_session.band.transport.tap("tap")
            self.assertTrue(wait_until(lambda: session.state()["status"]["moments"]))
            band_session.band.transport.tap("double-tap")
            self.assertTrue(wait_until(lambda: session.store.count() == 1))
            self.assertEqual(session.state()["people"][0]["name"], "Maya Chen")
        finally:
            if band is not None:
                band.shutdown()
            pipeline.stop()
            session.close()
            file.close()
