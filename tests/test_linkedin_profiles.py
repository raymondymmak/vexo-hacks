"""Offline contract regressions, not proof of live search discoverability.

The expected surnames and URLs are assertions/provider-response fixtures only;
lookup input contains first names and public contextual facts.
"""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import httpx
from remember.socials import lookup_social, _canonical_profile

CASES = json.loads((Path(__file__).parent / 'fixtures' / 'linkedin_people.json').read_text())


class LinkedInProfileTests(unittest.TestCase):
    def run_case(self, case, *, cited=True, confidence='high', found_name=None, matched=True):
        if not case.get('person'):
            self.skipTest(case['blocked_reason'])
        person = case['person']
        url = case['expected_url']
        facts = ([{'kind': 'employer', 'input_quote': person['company'],
                   'evidence': person['company'], 'source_url': url}] if person.get('company') else
                 [{'kind': kind, 'input_quote': person[key], 'evidence': person[key], 'source_url': url}
                  for kind, key in [('role', 'role'), ('project', 'working_on')]])
        result = {'url': url, 'name': found_name or case['full_name'], 'company': person.get('company', ''),
                  'confidence': confidence, 'matched_facts': facts if matched else []}
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

    def test_all_judge_targets_are_valid_personal_profiles(self):
        judges = [case for case in CASES if case.get('group') == 'judges']
        self.assertEqual(len(judges), 4)
        for case in judges:
            with self.subTest(person=case['id']):
                self.assertEqual(_canonical_profile(case['expected_url'] + '/'), case['expected_url'])

    def test_fixture_inputs_do_not_leak_target_identity(self):
        for case in CASES:
            if not case.get('person'):
                continue
            with self.subTest(person=case['id']):
                context = json.dumps(case['person']).lower()
                self.assertNotIn('linkedin.com', context)
                self.assertNotIn(case['full_name'].lower(), context)
                self.assertEqual(len(case['person']['name'].split()), 1)

    def test_deck_roster_has_every_named_person(self):
        expected = {'haris_malik', 'vikram_narra', 'kianif', 'asad_organizer',
                    'ivan_leo', 'gabby_ong', 'amit_vadi', 'peter_masone', 'ethan_black',
                    'luke_sine', 'daniel_tsentsiper', 'adam_proschek', 'sam_mcguire',
                    'pavitra_bhalla', 'amit_patil', 'max_selivonchyk', 'ashwin_mudaliar',
                    'abhi_aiyer', 'victor_su_ortiz', 'ming_hua', 'aarush_yadav',
                    'arnav_kumar', 'omar_sekkat', 'louis_hart_davis', 'karan_marv'}
        self.assertEqual({c['id'] for c in CASES if c.get('deck_source')}, expected)
        self.assertEqual(len({c['id'] for c in CASES}), len(CASES))
        for case in CASES:
            if case.get('person'):
                self.assertTrue(case.get('context_source'))
                self.assertEqual(_canonical_profile(case['expected_url']), case['expected_url'])
            else:
                self.assertTrue(case.get('blocked_reason'))
