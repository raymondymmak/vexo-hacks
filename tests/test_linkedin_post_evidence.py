import json
import unittest
import httpx
from remember.socials import _post_evidence, _search_query, lookup_social


class LinkedInPostEvidenceTests(unittest.TestCase):
    source = 'https://www.linkedin.com/posts/example-activity-123'
    profile = 'https://www.linkedin.com/in/actual-author'

    def document(self, **changes):
        result = {'@type': 'SocialMediaPosting',
                  'author': {'@type': 'Person', 'name': 'Alex Smith', 'url': self.profile},
                  'articleBody': 'Studied at UCLA and built FindSpot.'}
        result.update(changes)
        return '<script type="application/ld+json">' + json.dumps(result) + '</script>'

    def test_actual_author_link_not_post_slug(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=self.document()))) as client:
            evidence = _post_evidence({self.source}, client, 'Alex')
        self.assertEqual(evidence[0]['profile_url'], self.profile)
        self.assertIn('FindSpot', evidence[0]['text'])

    def test_comment_author_or_wrong_author_cannot_ground_identity(self):
        for doc in [self.document(author={'@type': 'Person', 'name': 'Another Person', 'url': self.profile}),
                    self.document(author={}, comment=[{'author': {'name': 'Alex Smith', 'url': self.profile}}]),
                    self.document(author={'@type': 'Person', 'name': 'Alex Smith', 'url': 'https://evil.example/in/alex'})]:
            with self.subTest(document=doc), httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=doc))) as client:
                self.assertEqual(_post_evidence({self.source}, client, 'Alex'), [])

    def test_untrusted_hosts_are_not_requested(self):
        def request(_):
            self.fail('Must not request an untrusted URL')
        with httpx.Client(transport=httpx.MockTransport(request)) as client:
            self.assertEqual(_post_evidence({'https://linkedin.com.evil.example/posts/test',
                                            'http://www.linkedin.com/posts/test',
                                            'https://user@www.linkedin.com/posts/test'}, client, 'Alex'), [])

    def test_redirect_failure_and_oversized_page_are_ignored(self):
        responses = [httpx.Response(302, headers={'location': 'https://example.com'}),
                     httpx.Response(999), httpx.Response(200, text='x' * (512 * 1024 + 1))]
        for response in responses:
            with self.subTest(status=response.status_code), httpx.Client(transport=httpx.MockTransport(lambda r: response)) as client:
                self.assertEqual(_post_evidence({self.source}, client, 'Alex'), [])

    def test_named_projects_and_past_employers_survive_query_building(self):
        self.assertIn('FindSpot', _search_query({'name': 'Alex', 'working_on': 'FindSpot navigation', 'summary': 'Studies at UCLA'}))
        self.assertIn('K-Scale Labs', _search_query({'name': 'Alex', 'summary': 'Worked at K-Scale Labs in California'}))

    def test_live_lookup_path_uses_cited_post_then_validates_original_context(self):
        person = {'name': 'Alex', 'summary': 'Studied at UCLA and built FindSpot.'}
        def request(r):
            if r.method == 'GET':
                self.assertEqual(str(r.url), self.source)
                return httpx.Response(200, text=self.document())
            payload = json.loads(r.content)
            if 'tools' in payload:
                return httpx.Response(200, json={'output': [
                    {'type': 'web_search_call', 'action': {'sources': [{'url': self.source}]}},
                    {'content': [{'type': 'output_text', 'text': 'Alex Smith wrote this post.'}]}]})
            self.assertIn(self.profile, payload['input'])
            facts = [{'kind': k, 'input_quote': q, 'evidence': q, 'source_url': self.source}
                     for k, q in [('education', 'UCLA'), ('project', 'FindSpot')]]
            result = {'url': self.profile, 'name': 'Alex Smith', 'confident': True, 'matched_facts': facts}
            return httpx.Response(200, json={'output': [{'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]})
        with httpx.Client(transport=httpx.MockTransport(request)) as client:
            self.assertEqual(lookup_social(person, api_key='test', client=client), self.profile)
