"""The Pipecat pipeline with the mock Deepgram service. No key and no band."""

import os
import tempfile
import time
import unittest
import wave

os.environ["REMEMBER_STT"] = "mock"
os.environ["REMEMBER_EXTRACTOR"] = "builtin"
os.environ.pop("DEEPGRAM_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)

from remember.clips import load_pcm, play_pcm
from remember.extract import HeuristicExtractor
from remember.session import Session
from remember.voice import MockDeepgramSTTService, VoicePipeline, build_stt


LINES = [
    "Hi, I'm Maya Chen. I'm working on a soil sensor for small farms.",
    "I care about field deployments and battery life.",
    "I'll send you the firmware notes by Friday.",
]


def wait_until(predicate, timeout=4):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".sqlite")
        self.session = Session(self.file.name, extractor=HeuristicExtractor())

    def tearDown(self):
        if getattr(self, "pipeline", None) is not None:
            self.pipeline.stop()
        self.session.close()
        self.file.close()

    def test_pcm_and_taps_become_a_card(self):
        self.pipeline = VoicePipeline(self.session, live=False, lines=LINES, bytes_per_line=32)
        self.assertIsInstance(self.pipeline.stt, MockDeepgramSTTService)
        self.pipeline.start()
        for _line in LINES:
            self.pipeline.feed_pcm(b"\x00" * 32)
        self.assertTrue(wait_until(lambda: "Friday" in self.session.state()["status"]["transcript"]))
        self.pipeline.feed_tap("tap")
        self.assertTrue(wait_until(lambda: self.session.state()["status"]["moments"]))
        self.pipeline.feed_tap("double-tap")
        self.assertTrue(wait_until(lambda: self.session.store.count() == 1))
        person = self.session.state()["people"][0]
        self.assertEqual(person["name"], "Maya Chen")
        self.assertIn("soil sensor", person["working_on"])
        self.assertIn("firmware", person["promises"][0]["what"])
        self.assertTrue(person["key_moments"])
        self.assertGreater(self.pipeline.stt.audio_bytes, 0)
        self.assertEqual(self.pipeline.processor.speakers, ["0", "1", "0"])

    def test_a_wav_clip_is_pcm_for_the_same_pipeline(self):
        path = os.path.join(tempfile.mkdtemp(), "clip.wav")
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 160)
        pcm = load_pcm(path)
        self.assertEqual(len(pcm), 320)
        self.pipeline = VoicePipeline(
            self.session, live=False, lines=["Hi, I'm Jordan Hale. I'm building a robotics lab."], bytes_per_line=320
        )
        self.pipeline.start()
        play_pcm(self.pipeline, pcm, chunk=320)
        self.assertTrue(wait_until(lambda: "Jordan Hale" in self.session.state()["status"]["transcript"]))
        self.pipeline.feed_tap("double-tap")
        self.assertTrue(wait_until(lambda: self.session.store.count() == 1))
        self.assertEqual(self.session.state()["people"][0]["name"], "Jordan Hale")


class DeepgramBuilderTests(unittest.TestCase):
    def test_offline_builder_is_the_mock(self):
        stt = build_stt("")
        self.assertIsInstance(stt, MockDeepgramSTTService)

    def test_live_builder_selects_deepgram_with_interim_results_and_diarization(self):
        import remember.voice as voice

        captured = {}

        class Capture:
            Settings = voice.DeepgramSTTService.Settings

            def __init__(self, **kwargs):
                captured.update(kwargs)

        original = voice.DeepgramSTTService
        voice.DeepgramSTTService = Capture
        try:
            stt = voice.build_stt(api_key="x")
        finally:
            voice.DeepgramSTTService = original
        self.assertIsInstance(stt, Capture)
        self.assertEqual(captured["encoding"], "linear16")
        self.assertEqual(captured["sample_rate"], 16000)
        self.assertTrue(captured["mip_opt_out"])
        self.assertEqual(captured["settings"].model, "nova-3-general")
        self.assertTrue(captured["settings"].interim_results)
        self.assertTrue(captured["settings"].diarize)
        self.assertTrue(captured["settings"].punctuate)
