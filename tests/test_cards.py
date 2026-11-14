"""Cards, the store, and the scripted demo. No network and no band."""

import os
import tempfile
import unittest

os.environ["REMEMBER_EXTRACTOR"] = "builtin"
os.environ["REMEMBER_STT"] = "mock"

from remember.demo import play
from remember.extract import CARD_SCHEMA, HeuristicExtractor, OpenAIExtractor
from remember.session import Session
from remember.splitter import Splitter
from remember.store import Store


MAYA = (
    "Hi, I'm Maya Chen. I'm working on a soil sensor for small farms. "
    "I care about field deployments and battery life. "
    "I'll send you the firmware notes by Friday."
)


class ExtractTests(unittest.TestCase):
    def test_schema_is_strict(self):
        self.assertFalse(CARD_SCHEMA["additionalProperties"])
        self.assertEqual(set(CARD_SCHEMA["required"]), set(CARD_SCHEMA["properties"]))
        promises = CARD_SCHEMA["properties"]["promises"]["items"]
        self.assertFalse(promises["additionalProperties"])

    def test_reads_a_career_fair_introduction(self):
        card = HeuristicExtractor().extract(MAYA, ["The pilot has to be in the ground by Friday."])
        self.assertEqual(card["name"], "Maya Chen")
        self.assertIn("soil sensor", card["working_on"])
        self.assertIn("field deployments", card["cares_about"])
        self.assertIn("battery life", card["cares_about"])
        self.assertEqual(card["promises"], [])
        self.assertTrue(any("Friday" in moment for moment in card["key_moments"]))

    def test_openai_call_keeps_tapped_moments(self):
        seen = {}

        def fake(text, schema, instructions):
            seen["schema"] = schema
            self.assertIn("Maya", text)
            return {
                "name": "Maya Chen",
                "working_on": "a soil sensor",
                "cares_about": ["battery life"],
                "promises": [{"what": "send the notes", "by_when": "Friday"}],
                "deadlines": ["Friday"],
                "key_moments": [],
                "summary": "Maya Chen is working on a soil sensor.",
            }

        card = OpenAIExtractor(extract_fn=fake).extract(MAYA, ["The pilot has to be in the ground by Friday."])
        self.assertEqual(card["name"], "Maya Chen")
        self.assertIn("Friday", card["key_moments"][0])
        self.assertFalse(seen["schema"]["additionalProperties"])

    def test_a_failed_model_call_uses_the_built_in_reader(self):
        def boom(text, schema, instructions):
            raise RuntimeError("the model did not answer")

        card = OpenAIExtractor(extract_fn=boom).extract(MAYA, [])
        self.assertEqual(card["name"], "Maya Chen")
        self.assertIn("soil sensor", card["working_on"])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".sqlite")
        self.store = Store(self.file.name)

    def tearDown(self):
        self.store.close()
        self.file.close()

    def _card(self, name, working, promise):
        return {
            "name": name,
            "working_on": working,
            "cares_about": ["hardware"],
            "promises": [{"what": promise, "by_when": "Friday"}],
            "deadlines": ["Friday"],
            "key_moments": [],
            "summary": name + " is working on " + working + ".",
        }

    def test_explicit_identity_merges_person(self):
        first = self.store.merge(self._card("Maya Chen", "a soil sensor", "send the notes"), "one", [], "s", "e")
        second = self.store.merge(self._card("maya chen", "a soil sensor on a drone", "send the notes"), "two", [], "s", "e", person_id=first["id"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["conversation_count"], 2)
        self.assertEqual(len(second["promises"]), 1)
        self.assertIn("drone", second["working_on"])
        self.assertEqual(self.store.search("drone")[0]["name"], "Maya Chen")
        self.assertEqual(self.store.search("Mya Chen")[0]["id"], first["id"])

    def test_reset_clears_people(self):
        self.store.merge(self._card("Jordan Hale", "a robotics lab", "make an introduction"), "hi", [], "s", "e")
        self.store.reset()
        self.assertEqual(self.store.count(), 0)


class SplitterTests(unittest.TestCase):
    def test_double_tap_closes_and_a_tap_keeps_the_surrounding_words(self):
        splitter = Splitter()
        self.assertIsNone(splitter.double_tap())
        splitter.hear("Hi, I'm Maya Chen.", True)
        splitter.mark()
        splitter.hear("I'll send the notes by Friday.", True)
        closed = splitter.double_tap()
        self.assertIn("Maya Chen", closed.transcript)
        self.assertIn("Friday", closed.moments[0])
        self.assertIsNone(splitter.open)


class DemoTests(unittest.TestCase):
    def test_scripted_fair_keeps_encounters_separate(self):
        file = tempfile.NamedTemporaryFile(suffix=".sqlite")
        session = Session(file.name, extractor=HeuristicExtractor())
        try:
            saved = play(session)
            names = [person["name"] for person in saved]
            self.assertEqual(names, ["Maya Chen", "Jordan Hale", "Maya Chen"])
            people = session.state()["people"]
            self.assertEqual(len(people), 3)
            maya = next(person for person in people if person["name"] == "Maya Chen")
            self.assertEqual(maya["conversation_count"], 1)
            self.assertTrue(session.state("robotics")["people"])
            self.assertIn("drone", maya["working_on"])
        finally:
            session.close()
            file.close()
