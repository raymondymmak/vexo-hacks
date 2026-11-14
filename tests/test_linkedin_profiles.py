"""Offline contract regressions, not proof of live search discoverability.

The expected surnames and URLs are assertions/provider-response fixtures only;
lookup input contains first names and public contextual facts.
"""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import httpx
from remember.socials import lookup_social

CASES = json.loads((Path(__file__).parent / 'fixtures' / 'linkedin_people.json').read_text())


class LinkedInProfileTests(unittest.TestCase):
    def run_case(self, case, *, cited=True, confidence='high', found_name=None, matched=True):
        person = case['person']
        url = case['expected_url']
        result = {'url': url, 'name': found_name or case['full_name'], 'company': '',
                  'confidence': confidence, 'matched_context': {
                      key: person[key] for key in ('role', 'working_on', 'summary')} if matched else {}}
        def reply(request):
            prompt = json.loads(request.content)['contents'][0]['parts'][0]['text']
            self.assertIn(person['name'], prompt)
            self.assertIn(person['summary'], prompt)
            self.assertNotIn(case['full_name'], prompt)
            self.assertNotIn(url.split('/in/')[1], prompt)
            return httpx.Response(200, json={'candidates': [{
                'content': {'parts': [{'text': json.dumps(result)}]},
                'groundingMetadata': {'groundingChunks': [{'web': {'uri': url}}] if cited else []}}]})
        with patch.dict('os.environ', {'OPENAI_API_KEY': ''}), httpx.Client(transport=httpx.MockTransport(reply)) as client:
            return lookup_social(person, gemini_key='fake-unit-test-key', client=client)

    def test_first_name_and_context_return_expected_profiles(self):
        for case in CASES:
            with self.subTest(person=case['id']):
                self.assertEqual(self.run_case(case), case['expected_url'])

    def test_expected_profile_is_not_accepted_without_evidence(self):
        for case in CASES:
            for change in ({'cited': False}, {'confidence': 'low'}, {'matched': False}, {'found_name': 'Unrelated Person'}):
                with self.subTest(person=case['id'], change=change):
                    self.assertEqual(self.run_case(case, **change), '')
