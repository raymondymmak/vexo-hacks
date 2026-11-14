"""Command line, and the promise that keys and the kit stay out of git."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CliTests(unittest.TestCase):
    def test_demo_once_prints_the_two_people(self):
        database = tempfile.NamedTemporaryFile(suffix=".sqlite")
        database.close()
        env = os.environ.copy()
        env["REMEMBER_EXTRACTOR"] = "builtin"
        env["REMEMBER_STT"] = "mock"
        env.pop("OPENAI_API_KEY", None)
        env.pop("DEEPGRAM_API_KEY", None)
        done = subprocess.run(
            [sys.executable, "-m", "remember", "--demo", "--once", "--db", database.name],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Maya Chen", done.stdout)
        self.assertIn("Jordan Hale", done.stdout)
        os.remove(database.name)

    def test_clip_once_goes_through_the_offline_pipeline(self):
        database = tempfile.NamedTemporaryFile(suffix=".sqlite")
        database.close()
        env = os.environ.copy()
        env["REMEMBER_EXTRACTOR"] = "builtin"
        env["REMEMBER_STT"] = "mock"
        env.pop("OPENAI_API_KEY", None)
        env.pop("DEEPGRAM_API_KEY", None)
        clip = ROOT / "vexo-open-kit" / "dashboard" / "speech" / "sample-speech.opus"
        done = subprocess.run(
            [sys.executable, "-m", "remember", "--clip", str(clip), "--once", "--db", database.name],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Pipecat", done.stdout)
        self.assertIn("offline reader", done.stdout)
        self.assertIn("Maya Chen", done.stdout)
        self.assertIn("Jordan Hale", done.stdout)
        self.assertNotIn("DEEPGRAM_API_KEY", done.stdout + done.stderr)
        os.remove(database.name)

    def test_a_real_band_needs_a_number(self):
        env = os.environ.copy()
        env["REMEMBER_EXTRACTOR"] = "builtin"
        env["REMEMBER_STT"] = "mock"
        done = subprocess.run(
            [sys.executable, "-m", "remember", "--once"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertNotEqual(done.returncode, 0)

    def test_secrets_and_the_kit_are_ignored(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", gitignore)
        self.assertIn("vexo-open-kit/", gitignore)
        example = (ROOT / "env.example").read_text(encoding="utf-8")
        self.assertIn("DEEPGRAM_API_KEY=", example)
        self.assertIn("OPENAI_API_KEY=", example)
        self.assertNotIn("sk-", example)
        tracked = subprocess.run(
            ["git", "check-ignore", ".env", "vexo-open-kit"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertIn(".env", tracked.stdout)
        self.assertIn("vexo-open-kit", tracked.stdout)
