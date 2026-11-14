"""The web page and the recall API."""

import os
import socket
import tempfile
import threading
import time
import unittest

os.environ["REMEMBER_EXTRACTOR"] = "builtin"
os.environ["REMEMBER_STT"] = "mock"

import httpx
import uvicorn
from fastapi.testclient import TestClient

from remember.extract import HeuristicExtractor
from remember.session import Session
from remember.web import create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".sqlite")
        self.session = Session(self.file.name, extractor=HeuristicExtractor())
        self.client = TestClient(create_app(self.session, play_demo=lambda: True))

    def tearDown(self):
        self.session.close()
        self.file.close()

    def test_say_end_and_recall(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("innerHTML", page.text)
        self.assertIn("AfterHello", page.text)
        said = self.client.post("/api/say", json={"text": "Hi, I'm Maya Chen. I'm working on a soil sensor."})
        self.assertEqual(said.status_code, 200)
        self.client.post("/api/mark")
        ended = self.client.post("/api/end")
        self.assertEqual(ended.json()["person"]["name"], "Maya Chen")
        found = self.client.get("/api/state", params={"q": "soil"}).json()
        self.assertEqual(found["people"][0]["name"], "Maya Chen")
        self.assertEqual(self.client.get("/api/state", params={"q": "nobody-here"}).json()["people"], [])
        self.client.post("/api/reset")
        self.assertEqual(self.client.get("/api/state").json()["people"], [])

    def test_empty_line_is_rejected(self):
        self.assertEqual(self.client.post("/api/say", json={"text": "  "}).status_code, 400)

    def test_events_stream_a_card(self):
        """SSE has to be a real socket. TestClient holds one portal, so a stream and a post deadlock."""
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]
        holder.close()
        app = create_app(self.session, play_demo=lambda: True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time() + 5
        while time.time() < deadline and not server.started:
            time.sleep(0.02)
        self.assertTrue(server.started)
        received = []

        def read():
            with httpx.Client(timeout=5) as client:
                with client.stream("GET", "http://127.0.0.1:%d/api/events" % port) as response:
                    for line in response.iter_lines():
                        if isinstance(line, bytes):
                            line = line.decode()
                        if line.startswith("data:"):
                            received.append(line)
                            return

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        time.sleep(0.3)
        httpx.post(
            "http://127.0.0.1:%d/api/say" % port,
            json={"text": "Hi, I'm Jordan Hale. I'm building a robotics lab."},
            timeout=5,
        )
        httpx.post("http://127.0.0.1:%d/api/end" % port, timeout=5)
        reader.join(4)
        server.should_exit = True
        thread.join(3)
        self.assertTrue(received)
        self.assertIn("Jordan Hale", received[0])
