"""Pretend band only. These tests never open Bluetooth."""

import os
import tempfile
import time
import unittest

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
