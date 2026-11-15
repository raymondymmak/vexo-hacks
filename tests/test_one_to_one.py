import unittest
from fastapi.testclient import TestClient
from remember.session import Session
from remember.extract import HeuristicExtractor
from remember.web import create_app

class OneToOneTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(':memory:', HeuristicExtractor())
        self.client = TestClient(create_app(self.session, pairing_token='pair-test'))
        self.headers = {'Authorization': 'Bearer pair-test'}
    def tearDown(self):
        self.session.close()
    def post(self, path, body=None):
        return self.client.post(path, json=body, headers=self.headers)
    def test_pairing_protects_private_routes(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        self.assertEqual(self.client.post('/api/start').status_code, 401)
        self.assertEqual(self.client.get('/api/state', headers=self.headers).status_code, 200)
    def test_capture_review_export_and_explicit_revisit(self):
        self.post('/api/start')
        self.post('/api/say', {'speaker':'0','text':"I'm Tim. I'll send you my repo tomorrow."})
        self.post('/api/say', {'speaker':'1','text':"I'm Maya Chen. I'm building a soil sensor. Email maya@example.com."})
        self.post('/api/wearer', {'speaker':'0'})
        person = self.post('/api/end').json()['person']
        self.assertEqual(person['name'], 'Maya Chen')
        self.assertIn('repo', person['promises'][0]['what'])
        route = '/api/people/%d' % person['id']
        self.assertEqual(self.client.get(route+'/contact.vcf', headers=self.headers).status_code, 200)
        saved = self.client.patch(route, headers=self.headers, json={'reviewed': True, 'email':'maya@example.com'})
        self.assertEqual(saved.status_code, 200)
        vcf = self.client.get(route+'/contact.vcf', headers=self.headers)
        self.assertIn('EMAIL;TYPE=INTERNET:maya@example.com', vcf.text)
        self.assertIn('You promised:', vcf.text)
        self.post('/api/start')
        self.post('/api/say', {'text':"I'm Maya Chen. I'm building a drone."})
        again = self.post('/api/end', {'person_id': person['id']}).json()['person']
        self.assertEqual(again['conversation_count'], 2)
        self.assertFalse(again['reviewed'])
    def test_idle_audio_cannot_start_capture(self):
        self.session.hear('background conversation', speaker='0', audio=True)
        self.assertEqual(self.session.state()['status']['phase'], 'idle')
        self.assertEqual(self.session.state()['status']['transcript'], '')
    def test_handshake_is_idempotent_while_recording(self):
        self.post('/api/handshake')
        self.post('/api/say', {'text':"I'm Maya Chen."})
        self.post('/api/handshake')
        self.assertEqual(self.session.store.count(), 0)
        self.assertIn('Maya', self.session.state()['status']['transcript'])

    def test_cross_origin_controls_are_rejected(self):
        self.assertEqual(self.client.post('/api/start', headers={'Origin':'https://evil.example', **self.headers}).status_code, 403)
    def test_non_ascii_key_is_rejected(self):
        self.assertEqual(self.client.get('/api/events', params={'token':'é'}).status_code, 401)
