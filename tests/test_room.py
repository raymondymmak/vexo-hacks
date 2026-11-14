import json
import tempfile
import threading
import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient

from remember.room import Room, create_room_app


ADMIN = {'Authorization': 'Bearer admin-test'}
PROJECTOR = {'Authorization': 'Bearer projector-test'}


def matching(participants, speaker_id, card, marked=False):
    return [{'source_id': speaker_id, 'target_id': 'sam', 'kind': 'intro', 'reason': 'Private FPGA introduction context.'}]


class RoomTests(unittest.TestCase):
    def setUp(self):
        self.app = create_room_app(admin_token='admin-test', projector_token='projector-test', matcher=matching)
        self.client = TestClient(self.app)
        self.tokens = {}
        for name in ('alex', 'priya', 'sam'):
            r = self.client.post('/api/participants', headers=ADMIN, json={
                'id': name, 'name': name.title(), 'company': 'Private Company', 'needs': ['Private need'],
                'offers': ['Private offer'], 'simulated': True})
            self.assertEqual(r.status_code, 200)
            self.tokens[name] = {'Authorization': 'Bearer ' + r.json()['token']}

    def tearDown(self):
        self.app.state.room.close()

    def submit(self, event_id='event-1'):
        return self.client.post('/api/encounters', headers=self.tokens['alex'], json={
            'event_id': event_id, 'other_id': 'priya', 'marked': True,
            'card': {'summary': 'PRIVATE SUMMARY', 'name': 'Priya'}})

    def test_authentication_and_cross_origin_controls(self):
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        self.assertEqual(self.client.get('/api/state', headers=self.tokens['alex']).status_code, 401)
        self.assertEqual(self.client.get('/api/alerts', headers=PROJECTOR).status_code, 401)
        self.assertEqual(self.client.post('/api/reset', headers=PROJECTOR).status_code, 401)
        self.assertEqual(self.client.post('/api/reset', headers={**ADMIN, 'Origin': 'https://untrusted.example'}).status_code, 403)
        self.assertTrue(self.client.get('/api/state', headers=ADMIN).json()['can_control'])
        self.assertFalse(self.client.get('/api/state', headers=PROJECTOR).json()['can_control'])

    def test_map_is_public_safe_and_alerts_are_recipient_scoped(self):
        self.assertEqual(self.submit().status_code, 200)
        state = self.client.get('/api/state', headers=PROJECTOR).json()
        self.assertEqual(len(state['edges']), 2)
        for private in ('PRIVATE SUMMARY', 'Private need', 'Private offer', 'Private Company', 'Private FPGA'):
            self.assertNotIn(private, json.dumps(state))
        alex = self.client.get('/api/alerts', headers=self.tokens['alex']).json()['alerts']
        sam = self.client.get('/api/alerts', headers=self.tokens['sam']).json()['alerts']
        self.assertEqual(len(alex), 1)
        self.assertEqual(len(sam), 1)
        self.assertEqual(alex[0]['other']['id'], 'sam')
        self.assertEqual(self.client.get('/api/alerts', headers=self.tokens['priya']).json()['alerts'], [])
        self.assertEqual(self.client.post('/api/alerts/%d/ack' % alex[0]['id'], headers=self.tokens['priya']).status_code, 404)
        for _ in range(2):
            self.assertEqual(self.client.post('/api/alerts/%d/ack' % alex[0]['id'], headers=self.tokens['alex']).status_code, 200)
        self.assertEqual(self.client.get('/api/alerts', headers=self.tokens['alex']).json()['alerts'], [])
        self.assertEqual(len(self.client.get('/api/alerts', headers=self.tokens['sam']).json()['alerts']), 1)

    def test_duplicate_events_and_pairs_do_not_repeat_alerts(self):
        self.assertFalse(self.submit().json()['duplicate'])
        self.assertTrue(self.submit().json()['duplicate'])
        self.assertFalse(self.submit('event-2').json()['duplicate'])
        self.assertEqual(len(self.app.state.room.state()['edges']), 2)
        self.assertEqual(len(self.app.state.room.alerts('alex')['alerts']), 1)

    def test_reset_invalidates_inflight_matching_and_tokens(self):
        entered, proceed = threading.Event(), threading.Event()
        def delayed(*args, **kwargs):
            entered.set()
            proceed.wait(5)
            return matching(*args, **kwargs)
        self.app.state.room.matcher = delayed
        result = []
        worker = threading.Thread(target=lambda: result.append(self.submit()))
        worker.start()
        self.assertTrue(entered.wait(2))
        self.assertEqual(self.client.post('/api/reset', headers=ADMIN).status_code, 200)
        proceed.set()
        worker.join(3)
        self.assertEqual(result[0].status_code, 409)
        self.assertEqual(self.app.state.room.state()['edges'], [])
        self.assertEqual(self.client.get('/api/alerts', headers=self.tokens['alex']).status_code, 401)

    def test_input_bounds_and_raw_dialogue_are_rejected(self):
        self.assertEqual(self.client.post('/api/encounters', headers=self.tokens['alex'], json={
            'event_id': 'x', 'card': {'transcript': 'Do not send raw dialogue'}}).status_code, 422)
        self.assertEqual(self.client.post('/api/participants', headers=ADMIN, json={
            'id': 'x', 'name': 'x', 'needs': ['a' * 501]}).status_code, 400)
        self.assertEqual(self.client.post('/api/encounters', headers=self.tokens['alex'], json={
            'event_id': 'x', 'other_id': 'unregistered'}).status_code, 400)

    def test_card_name_resolves_meeting_but_ambiguous_names_do_not(self):
        self.app.state.room.matcher = lambda *args, **kwargs: []
        response = self.client.post('/api/encounters', headers=self.tokens['alex'], json={
            'event_id': 'named', 'card': {'name': 'Priya', 'summary': 'A conversation'}})
        self.assertEqual([edge['kind'] for edge in response.json()['edges']], ['meeting'])
        self.client.post('/api/participants', headers=ADMIN, json={'id': 'sam2', 'name': 'Sam Jones'})
        response = self.client.post('/api/encounters', headers=self.tokens['alex'], json={
            'event_id': 'ambiguous', 'card': {'name': 'Sam'}})
        self.assertEqual(response.json()['edges'], [])

    def test_unrelated_or_invalid_match_pairs_are_not_published(self):
        self.app.state.room.matcher = lambda *args, **kwargs: [
            {'source_id': 'priya', 'target_id': 'sam', 'kind': 'match', 'reason': 'Unrelated'},
            {'source_id': 'alex', 'target_id': 'missing', 'kind': 'intro', 'reason': 'Invalid'}]
        response = self.client.post('/api/encounters', headers=self.tokens['alex'], json={'event_id': 'x'})
        self.assertEqual(response.json()['edges'], [])

    def test_conversation_facts_persist_and_match_a_later_encounter(self):
        from remember.room_matching import match_room
        room = Room(matcher=lambda *args, **kwargs: match_room(*args, **kwargs, use_model=False))
        try:
            for identifier, name in [('alex', 'Alex Chen'), ('priya', 'Priya Shah'), ('sam', 'Sam Rivera')]:
                room.register({'id': identifier, 'name': name})
            room.encounter('priya', {'event_id': 'hiring', 'card': {
                'name': 'Sam Rivera', 'summary': 'Sam Rivera is hiring FPGA interns.'}})
            self.assertTrue(next(p for p in room.participants() if p['id'] == 'sam')['offers'])
            result = room.encounter('priya', {'event_id': 'seeking', 'card': {
                'name': 'Alex Chen', 'summary': 'Alex Chen is looking for an FPGA internship.'}})
            matches = [edge for edge in result['edges'] if edge['kind'] == 'match']
            self.assertEqual(len(matches), 1)
            self.assertEqual({matches[0]['source_id'], matches[0]['target_id']}, {'alex', 'sam'})
            self.assertEqual(room.alerts('priya')['alerts'], [])
            self.assertEqual(len(room.alerts('alex')['alerts']), 1)
        finally:
            room.close()

    def test_restart_retains_graph_tokens_and_acknowledgements(self):
        with tempfile.TemporaryDirectory() as folder:
            path = folder + '/room.sqlite'
            room = Room(path, matching)
            auth = room.register({'id': 'alex', 'name': 'Alex'})['token']
            room.register({'id': 'sam', 'name': 'Sam'})
            room.encounter('alex', {'event_id': 'encounter'})
            alert_id = room.alerts('alex')['alerts'][0]['id']
            room.ack('alex', alert_id)
            room.close()
            room = Room(path, matching)
            try:
                self.assertEqual(room.authenticate_participant(auth), 'alex')
                self.assertEqual(len(room.state()['edges']), 1)
                self.assertEqual(room.alerts('alex')['alerts'], [])
                self.assertTrue(room.encounter('alex', {'event_id': 'encounter'})['duplicate'])
            finally:
                room.close()

    def test_demo_registration_is_explicit_and_repeatable(self):
        result = self.client.post('/api/demo', headers=ADMIN, json={'reset': True})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json()['state']['participants']), 3)
        self.assertEqual(set(result.json()['participant_tokens']), {'alex', 'priya', 'sam'})
        self.assertEqual(self.client.post('/api/demo', headers=ADMIN, json={}).status_code, 409)
        self.assertEqual(self.client.post('/api/demo', headers=ADMIN, json={'reset': True}).status_code, 200)
