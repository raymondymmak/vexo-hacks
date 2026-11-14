import json
import os
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from remember.contact_flow import ContactFlow, contact_key
from remember.extract import HeuristicExtractor
from remember.session import Session
from remember.socials import lookup_social
from remember.web import create_app

class SocialTests(unittest.TestCase):
    def lookup(self, result, source):
        def reply(request):
            return httpx.Response(200, json={'output':[
                {'action':{'sources':[{'url':source}]}},
                {'content':[{'type':'output_text','text':json.dumps(result)}]}]})
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            return lookup_social({'name':'Sam Smith','company':'Acme'},api_key='test',client=client)
    def test_only_cited_matching_profile_is_used(self):
        url='https://www.linkedin.com/in/sam-smith'
        result={'url':url,'name':'Sam Smith','company':'Acme','confident':True}
        self.assertEqual(self.lookup(result,url),url)
        self.assertEqual(self.lookup(result,'https://example.com'), '')
        self.assertEqual(self.lookup(dict(result,company='Other'),url),'')
        self.assertEqual(self.lookup(dict(result,confident=False),url),'')
    def test_unknown_person_does_not_search(self):
        self.assertEqual(lookup_social({'name':'Sam'},api_key='test'), '')

class GeminiSocialTests(unittest.TestCase):
    URL = 'https://www.linkedin.com/in/sam-smith'
    REDIRECT = 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc'

    def lookup(self, result=None, chunks=None, target=None, status=200):
        result = dict({'url': self.URL, 'name': 'Sam Smith', 'company': 'Acme', 'confidence': 'high'}, **(result or {}))
        if chunks is None:
            chunks = [{'web': {'uri': self.REDIRECT, 'title': 'linkedin.com'}}]
        self.requests = []
        def reply(request):
            self.requests.append(request)
            if request.method == 'GET':
                return httpx.Response(302, headers={'location': target or self.URL})
            return httpx.Response(status, json={'candidates': [{
                'content': {'parts': [{'text': '```json\n' + json.dumps(result) + '\n```'}]},
                'groundingMetadata': {'groundingChunks': chunks}}]})
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            return lookup_social({'name': 'Sam Smith', 'company': 'Acme'}, gemini_key='g-key', client=client)

    def test_high_confidence_grounded_profile_is_used(self):
        self.assertEqual(self.lookup(), self.URL)
        post = self.requests[0]
        self.assertIn('generativelanguage.googleapis.com', str(post.url))
        self.assertNotIn('g-key', str(post.url))
        self.assertEqual(post.headers['x-goog-api-key'], 'g-key')
        body = json.loads(post.content)
        self.assertEqual(body['tools'], [{'google_search': {}}])
        self.assertIn('Sam Smith', body['contents'][0]['parts'][0]['text'])

    def test_direct_source_matches_but_x_profile_is_rejected(self):
        self.assertEqual(self.lookup(chunks=[{'web': {'uri': 'https://linkedin.com/in/Sam-Smith/', 'title': 'x'}}]), self.URL)
        x = 'https://x.com/samsmith'
        self.assertEqual(self.lookup({'url': x}, [{'web': {'uri': self.REDIRECT, 'title': 'twitter.com'}}],
                                     target='https://twitter.com/samsmith'), '')

    def test_low_or_medium_confidence_stays_blank(self):
        self.assertEqual(self.lookup({'confidence': 'medium'}), '')
        self.assertEqual(self.lookup({'confidence': 'low'}), '')
        self.assertEqual(self.lookup({'confidence': True}), '')
        self.assertEqual(len(self.requests), 1)

    def test_ungrounded_or_mismatched_profile_stays_blank(self):
        self.assertEqual(self.lookup(chunks=[]), '')
        self.assertEqual(self.lookup(target='https://www.linkedin.com/in/someone-else'), '')
        self.assertEqual(self.lookup(chunks=[{'web': {'uri': self.REDIRECT, 'title': 'Sam Smith'}}]), self.URL)
        self.assertEqual(self.lookup({'company': 'Other'}), '')
        self.assertEqual(self.lookup({'url': 'https://example.com/in/sam-smith'}), '')

    def test_errors_stay_blank(self):
        self.assertEqual(self.lookup(status=500), '')
        self.assertEqual(self.lookup(status=429), '')

    def test_first_name_and_context_accept_grounded_regional_profile(self):
        person = {'name': 'Sam', 'role': 'EECS student at Berkeley', 'summary': 'First-year student from Hong Kong'}
        url = 'https://hk.linkedin.com/in/sam-smith?trk=search'
        def reply(request):
            result = {'url': url, 'name': 'Sam Smith', 'confidence': 'high',
                      'matched_context': {'role': person['role'], 'summary': person['summary']}}
            return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(result)}]},
                'groundingMetadata': {'groundingChunks': [{'web': {'uri': url}}]}}]})
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            self.assertEqual(lookup_social(person, gemini_key='test', client=client), self.URL)

    def test_gemini_timeout_uses_cited_search_fallback(self):
        seen = []
        def reply(request):
            seen.append(request.url.host)
            if request.url.host == 'generativelanguage.googleapis.com':
                raise httpx.ReadTimeout('test', request=request)
            result = {'url': self.URL, 'name': 'Sam Smith', 'company': 'Acme', 'confident': True}
            return httpx.Response(200, json={'output': [
                {'action': {'sources': [{'url': self.URL}]}},
                {'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]})
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            errors = []
            self.assertEqual(lookup_social({'name': 'Sam Smith', 'company': 'Acme'}, gemini_key='g', api_key='o', client=client, on_error=errors.append), self.URL)
            self.assertEqual(errors, [])
            self.assertEqual(seen, ['generativelanguage.googleapis.com', 'api.openai.com'])

    def test_cited_prose_is_structured_without_losing_source_validation(self):
        seen = []
        def reply(request):
            body = json.loads(request.content); seen.append(body)
            if body.get('tools'):
                return httpx.Response(200, json={'output': [
                    {'action': {'sources': [{'url': self.URL}]}},
                    {'content': [{'type': 'output_text', 'text': 'Sam Smith at Acme: ' + self.URL}]}]})
            self.assertEqual(body['text']['format']['type'], 'json_object')
            result = {'url': self.URL, 'name': 'Sam Smith', 'company': 'Acme', 'confident': True}
            return httpx.Response(200, json={'output': [{'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]})
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            self.assertEqual(lookup_social({'name': 'Sam Smith', 'company': 'Acme'}, api_key='test', client=client), self.URL)
        self.assertEqual(len(seen), 2)

    def test_timeout_is_reported_explicitly(self):
        errors = []
        def timeout(request):
            raise httpx.ReadTimeout('test', request=request)
        with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
            result = lookup_social({'name': 'Sam Smith', 'company': 'Acme'}, gemini_key='test', client=client, on_error=errors.append)
        self.assertEqual(result, '')
        self.assertIn('timed out', errors[0])
        self.assertNotIn('unreadable', errors[0])

    def test_context_can_identify_linkedin_without_company(self):
        person = {'name': 'Sam Smith', 'role': 'Robotics engineer', 'working_on': 'Underwater inspection drones',
                  'transcript': 'PRIVATE RAW WORDS', 'email': 'private@example.com'}
        matched = {'role': person['role'], 'working_on': person['working_on']}
        def reply(request):
            prompt = json.loads(request.content)['contents'][0]['parts'][0]['text']
            self.assertIn('Underwater inspection drones', prompt)
            self.assertNotIn('PRIVATE RAW WORDS', prompt)
            self.assertNotIn('private@example.com', prompt)
            return httpx.Response(200, json={'candidates': [{
                'content': {'parts': [{'text': json.dumps({'url': self.URL, 'name': 'Sam Smith',
                    'confidence': 'high', 'matched_context': matched})}]},
                'groundingMetadata': {'groundingChunks': [{'web': {'uri': self.URL}}]}}]})
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            self.assertEqual(lookup_social(person, gemini_key='test', client=client), self.URL)
            matched.pop('working_on')
            self.assertEqual(lookup_social(person, gemini_key='test', client=client), '')

    def test_gemini_key_in_environment_selects_gemini(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'env-key', 'OPENAI_API_KEY': 'openai'}):
            seen = []
            def reply(request):
                seen.append(request)
                return httpx.Response(200, json={'candidates': []})
            with httpx.Client(transport=httpx.MockTransport(reply)) as client:
                self.assertEqual(lookup_social({'name': 'Sam Smith', 'company': 'Acme'}, client=client), '')
            self.assertEqual(seen[0].headers['x-goog-api-key'], 'env-key')
            self.assertEqual(lookup_social({'name': 'Sam'}), '')

class FlowTests(unittest.TestCase):
    def setUp(self):
        self.session=Session(':memory:', HeuristicExtractor())
        self.calls=[]
        outer=self
        class Service:
            def deliver(self,person,text,fallback_url=None):
                outer.calls.append((person,text,fallback_url))
                return {'icloud':{'status':'saved'},'notification':{'status':'sent'}}
        self.service=Service()
    def tearDown(self):
        self.session.close()
    def capture(self):
        self.session.hear("I'm Maya Chen. I work at Acme.")
        return self.session.end()
    def test_offline_never_delivers(self):
        flow=ContactFlow(self.session,offline=True,service=self.service)
        self.session.on_contact_ready=flow.enqueue
        person=self.capture()
        flow.close()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.session.store.get(person['id'])['delivery']['icloud']['status'],'disabled')
    def test_delivery_and_scoped_notification_download(self):
        flow=ContactFlow(self.session,service=self.service,secret='secret',lookup=lambda p:'')
        self.session.on_contact_ready=flow.enqueue
        with patch.dict(os.environ, {'REMEMBER_PUBLIC_URL':'https://remember.example'}):
            person=self.capture()
            flow.close()
        self.assertEqual(len(self.calls),1)
        self.assertIn('BEGIN:VCARD',self.calls[0][1])
        self.assertIn('/contact/',self.calls[0][2])
        stored=self.session.store.get(person['id'])
        self.assertEqual(stored['delivery']['icloud']['status'],'saved')
        self.assertIn('Maya', stored['conversations'][0]['transcript'])
        self.assertNotIn('[unknown]', self.calls[0][1])
        client=TestClient(create_app(self.session,pairing_token='pair',download_secret='secret'))
        route='/contact/%d.vcf' % person['id']
        self.assertEqual(client.get(route).status_code,404)
        self.assertEqual(client.get(route,params={'key':contact_key('secret',person)}).status_code,200)
        self.assertEqual(client.get('/api/state',params={'token':contact_key('secret',person)}).status_code,401)

class FlowRaceTests(unittest.TestCase):
    def setUp(self):
        import threading
        self.session = Session(':memory:', HeuristicExtractor())
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
        outer = self
        class Service:
            def deliver(self, person, text, fallback_url=None):
                outer.calls.append(person)
                return {'icloud': {'status': 'saved'}, 'notification': {'status': 'sent'}}
        self.service = Service()

    def tearDown(self):
        self.session.close()

    def person(self, name='Maya Chen'):
        return self.session.store.merge({'name': name}, '', [], 's', 'e')

    def blocking_lookup(self, person):
        self.entered.set()
        self.release.wait(3)
        return 'https://www.linkedin.com/in/found-person'

    def test_reset_reused_id_prevents_old_send_and_writeback(self):
        flow = ContactFlow(self.session, service=self.service, lookup=self.blocking_lookup)
        try:
            with patch.dict(os.environ, {'REMEMBER_AUTO_DELIVER': '1', 'REMEMBER_SOCIAL_LOOKUP': '1'}):
                original = self.person()
                flow.enqueue(original)
                self.assertTrue(self.entered.wait(1))
                # Exercise UID defense independently of the reset hook.
                self.session.store.reset()
                replacement = self.person('Jordan Hale')
                self.assertEqual(replacement['id'], original['id'])
                self.release.set()
                flow.close()
            self.assertEqual(self.calls, [])
            stored = self.session.store.get(replacement['id'])
            self.assertEqual(stored['name'], 'Jordan Hale')
            self.assertFalse(stored['delivery'])
            self.assertEqual(stored['social_url'], '')
            self.assertEqual(len(flow.futures), 0)
        finally:
            self.release.set()
            flow.close()

    def test_user_social_edit_wins_over_lookup(self):
        flow = ContactFlow(self.session, service=self.service, lookup=self.blocking_lookup)
        try:
            with patch.dict(os.environ, {'REMEMBER_AUTO_DELIVER': '1', 'REMEMBER_SOCIAL_LOOKUP': '1'}):
                person = self.person()
                flow.enqueue(person)
                self.assertTrue(self.entered.wait(1))
                user_url = 'https://www.linkedin.com/in/user-selected'
                self.session.store.update_person(person['id'], {'social_url': user_url})
                self.release.set()
                flow.close()
            self.assertEqual(self.session.store.get(person['id'])['social_url'], user_url)
            self.assertEqual(self.calls[0]['social_url'], user_url)
        finally:
            self.release.set()
            flow.close()

    def test_reset_hook_cancels_queued_and_invalidates_running(self):
        flow = ContactFlow(self.session, service=self.service, lookup=self.blocking_lookup)
        try:
            with patch.dict(os.environ, {'REMEMBER_AUTO_DELIVER': '1', 'REMEMBER_SOCIAL_LOOKUP': '1'}):
                flow.enqueue(self.person())
                self.assertTrue(self.entered.wait(1))
                flow.enqueue(self.person('Jordan Hale'))
                flow.cancel_pending()
                self.release.set()
                flow.close()
            self.assertEqual(self.calls, [])
            self.assertEqual(flow.futures, set())
        finally:
            self.release.set()
            flow.close()

    def test_unknown_without_contact_details_skips_provider(self):
        flow = ContactFlow(self.session, service=self.service)
        person = self.person('Someone you met')
        flow.enqueue(person)
        flow.close()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.session.store.get(person['id'])['delivery']['icloud']['status'], 'skipped')

    def test_metadata_uid_guard_and_empty_lookup(self):
        person = self.person()
        self.session.store.update_person(person['id'], {'social_url': 'https://www.linkedin.com/in/saved'})
        self.assertIsNone(self.session.store.set_delivery(person['id'], {'bad': True}, contact_uid='wrong'))
        stored = self.session.store.set_delivery(person['id'], {'icloud': {'status': 'saved'}}, '',
            contact_uid=person['contact_uid'], original_social_url='https://www.linkedin.com/in/saved')
        self.assertEqual(stored['social_url'], 'https://www.linkedin.com/in/saved')

    def test_provider_already_started_cannot_write_to_reused_id(self):
        outer = self
        class BlockingService:
            def deliver(self, person, text, fallback_url=None):
                outer.entered.set()
                outer.release.wait(3)
                return {'icloud': {'status': 'saved'}, 'notification': {'status': 'sent'}}
        flow = ContactFlow(self.session, service=BlockingService(), lookup=lambda p: '')
        try:
            original = self.person()
            flow.enqueue(original)
            self.assertTrue(self.entered.wait(1))
            flow.cancel_pending()
            self.session.store.reset()
            replacement = self.person('Jordan Hale')
            self.release.set()
            flow.close()
            stored = self.session.store.get(replacement['id'])
            self.assertFalse(stored['delivery'])
            self.assertNotEqual(stored['contact_uid'], original['contact_uid'])
        finally:
            self.release.set()
            flow.close()

class SignedLinkTests(unittest.TestCase):
    def test_expired_and_unicode_links_fail(self):
        import time
        from remember.contact_flow import valid_contact_key
        person = {'contact_uid':'test-uid'}
        self.assertTrue(valid_contact_key('secret', person, contact_key('secret', person)))
        self.assertFalse(valid_contact_key('secret', person, contact_key('secret', person, int(time.time())-1)))
        self.assertFalse(valid_contact_key('secret', person, 'é'))
