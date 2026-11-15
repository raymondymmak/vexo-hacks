import unittest
from remember.socials import _context_matches, _cited_facts, _rejection


class FactMatchingTests(unittest.TestCase):
    def setUp(self):
        self.context = {'name': 'Raymond', 'summary': 'Raymond studies at Berkeley and is from Hong Kong. He builds RISC-V cores.'}
        self.url = 'https://www.linkedin.com/in/example'
        self.facts = [self.fact('education', 'Berkeley'), self.fact('location', 'Hong Kong')]

    def fact(self, kind, quote):
        return {'kind': kind, 'input_quote': quote, 'evidence': quote, 'source_url': self.url}

    def test_two_facts_in_one_summary_without_project_match(self):
        result = _cited_facts({'matched_facts': self.facts}, {self.url})
        self.assertTrue(_context_matches(result, self.context))

    def test_duplicate_education_cannot_count_twice(self):
        self.assertFalse(_context_matches({'matched_facts': [self.facts[0], self.facts[0]]}, self.context))

    def test_uncited_evidence_is_rejected(self):
        result = _cited_facts({'matched_facts': self.facts}, {'https://example.com'})
        self.assertFalse(_context_matches(result, self.context))

    def test_invented_context_is_rejected(self):
        self.assertFalse(_context_matches({'matched_facts': [self.facts[0], self.fact('location', 'London')]}, self.context))

    def test_conflicting_or_ambiguous_candidate_is_rejected(self):
        for extra in ({'ambiguous': True}, {'conflicts': ['Different university']}):
            self.assertFalse(_context_matches(dict(matched_facts=self.facts, **extra), self.context))

    def test_missing_citation_has_specific_reason(self):
        result = {'url': self.url, 'name': 'Raymond Mak', 'matched_facts': self.facts}
        self.assertIn('citations', _rejection(result, self.context, False))

    def test_query_uses_clues_without_instruction_paragraph(self):
        from remember.socials import _search_query
        context = dict(self.context, role='Major at Berkeley (field unclear)', working_on='Firmware and RISC-V')
        self.assertEqual(_search_query(context), 'LinkedIn Raymond Berkeley Hong Kong')
        self.assertEqual(_search_query(context, True), 'site:linkedin.com/in Raymond Berkeley Firmware')

    def test_tracking_parameters_do_not_discard_cited_fact(self):
        result = _cited_facts({'matched_facts': self.facts}, {self.url + '?utm_source=openai'})
        self.assertTrue(_context_matches(result, self.context))

    def test_confirmed_identity_reused_only_with_two_clues(self):
        from remember.socials import confirmed_profile
        known = dict(self.context, name='Raymond Mak', reviewed=True, social_url=self.url)
        self.assertEqual(confirmed_profile(self.context, [known]), self.url)
        self.assertEqual(confirmed_profile({'name': 'Raymond', 'role': 'Berkeley'}, [known]), '')
        self.assertEqual(confirmed_profile(self.context, [dict(known, reviewed=False)]), '')
        self.assertEqual(confirmed_profile(self.context, [known, dict(known, social_url=self.url+'2')]), '')
