"""Offline identity regression cases; these do not claim to exercise an AI model."""
import unittest

from remember.extract import HeuristicExtractor, OpenAIExtractor


TIMOTHY = '''[speaker 0] Hi. Nice to meet you, ma’am. I’m Raymond. Hey. I’m Timothy. How are you?
[speaker 0] Yeah. So I am a developer evangelist at sima.ai.
[speaker 0] It’s essentially a silicon company. Based in San Jose. And for school, I went to UCLA.
[speaker 1] The first part of the demo video should be a short introduction. I'm working on the demo video.
[speaker 1] I'll send you the video by Friday.'''

RAYMOND = '''[speaker 0] Hi, I'm Timothy. Nice to meet you. I'm Raymond.
[speaker 0] I am an EECS student at Berkeley. I went to UC Berkeley.
[speaker 1] For the pitch we should show the dashboard. I'm working on a demo video.'''


class ExtractionIdentityTests(unittest.TestCase):
    def test_second_introduction_with_professional_details_survives_merged_speaker(self):
        card = HeuristicExtractor().extract(TIMOTHY)
        self.assertEqual(card['name'], 'Timothy')
        self.assertEqual(card['company'], 'sima.ai')
        self.assertEqual(card['role'], 'developer evangelist')
        self.assertIn('UCLA', card['summary'])
        self.assertIn('San Jose', card['summary'])
        self.assertNotIn('demo video', card['summary'])
        self.assertNotIn('demo video', card['working_on'])
        self.assertEqual(card['promises'], [])

    def test_reversed_introductions_preserve_raymond_education(self):
        card = HeuristicExtractor().extract(RAYMOND)
        self.assertEqual(card['name'], 'Raymond')
        self.assertIn('EECS', card['summary'])
        self.assertIn('Berkeley', card['summary'])
        self.assertNotIn('demo video', card['summary'])

    def test_equally_supported_people_remain_ambiguous(self):
        card = HeuristicExtractor().extract("I'm Raymond. I work at Acme. I'm Timothy. I work at Beta.")
        self.assertEqual(card['name'], '')
        self.assertEqual(card['company'], '')
        uneven = HeuristicExtractor().extract("I'm Raymond. I work at Acme. I went to Berkeley. I'm Timothy. I work at Beta.")
        self.assertEqual(uneven['name'], '')

    def test_wearer_is_excluded_even_when_they_have_more_biography(self):
        text = "[wearer] I'm Raymond. I am an engineer at Acme. I went to Berkeley.\n[other] I'm Timothy. I work at sima.ai."
        card = HeuristicExtractor().extract(text)
        self.assertEqual(card['name'], 'Timothy')
        self.assertEqual(card['company'], 'sima.ai')

    def test_blank_model_result_is_repaired_from_explicit_biography_not_chatter(self):
        # This tests deterministic postprocessing, not model comprehension.
        extractor = OpenAIExtractor(extract_fn=lambda *_: {'name': '', 'summary': 'Planning the demo video.'})
        card = extractor.extract(TIMOTHY)
        self.assertEqual(card['name'], 'Timothy')
        self.assertEqual(card['company'], 'sima.ai')
        self.assertIn('UCLA', card['summary'])
        self.assertEqual(card['promises'], [])

    def test_ambiguous_blank_model_result_is_not_assigned_a_name(self):
        extractor = OpenAIExtractor(extract_fn=lambda *_: {'name': ''})
        card = extractor.extract("I'm Raymond. I'm Timothy. We are planning the demo video.")
        self.assertEqual(card['name'], '')
