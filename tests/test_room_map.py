import os
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from remember.session import Session
from remember.extract import HeuristicExtractor
from remember.web import create_app

class RoomMapTests(unittest.TestCase):
    def test_paired_proxy_exposes_only_public_map_fields(self):
        session=Session(':memory:',HeuristicExtractor())
        self.addCleanup(session.close)
        def reply(request):
            self.assertEqual(request.headers['authorization'],'Bearer projector-secret')
            return httpx.Response(200,json={'participants':[{'id':'a','name':'Alex','simulated':True,'email':'private'}], 'edges':[{'id':1,'kind':'intro','source_id':'a','target_id':'a','reason':'private'}], 'token':'secret'})
        client_type=httpx.AsyncClient
        with patch.dict(os.environ,{'REMEMBER_ROOM_MAP_URL':'http://room.test','REMEMBER_ROOM_MAP_TOKEN':'projector-secret'}), patch('remember.web.httpx.AsyncClient',side_effect=lambda **kw:client_type(transport=httpx.MockTransport(reply),**kw)):
            client=TestClient(create_app(session,pairing_token='pair'))
            self.assertEqual(client.get('/api/room/map').status_code,401)
            result=client.get('/api/room/map',headers={'Authorization':'Bearer pair'})
            self.assertEqual(result.status_code,200)
            self.assertTrue(result.json()['connected'])
            self.assertNotIn('private',result.text)
            self.assertNotIn('secret',result.text)

    def test_unconfigured_map_has_honest_empty_state(self):
        session=Session(':memory:',HeuristicExtractor())
        self.addCleanup(session.close)
        with patch.dict(os.environ,{'REMEMBER_ROOM_MAP_URL':'','REMEMBER_ROOM_MAP_TOKEN':''}):
            result=TestClient(create_app(session)).get('/api/room/map').json()
            self.assertFalse(result['connected'])
            self.assertEqual(result['participants'],[])
