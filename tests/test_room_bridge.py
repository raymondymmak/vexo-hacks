import tempfile
import unittest
from unittest.mock import Mock
import httpx
from remember.session import Session
from remember.extract import HeuristicExtractor
from remember.room_bridge import RoomBridge


class RoomBridgeTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(':memory:', HeuristicExtractor())
        self.person = {'contact_uid':'test-uid', 'name':'Sam', 'company':'Acme', 'summary':'Builds FPGA tools.',
                       'conversations':[{'id':1,'transcript':'PRIVATE RAW WORDS'}], 'email':'private@example.com'}

    def tearDown(self):
        self.session.close()

    def test_outage_retries_persisted_summary_without_raw_dialogue(self):
        with tempfile.NamedTemporaryFile(suffix='.sqlite') as file:
            def offline(request):
                raise httpx.ConnectError('offline',request=request)
            with httpx.Client(transport=httpx.MockTransport(offline)) as client:
                bridge = RoomBridge(self.session,'http://room.test','token',db_path=file.name,client=client)
                bridge.enqueue(self.person)
                bridge.tick()
                self.assertEqual(self.session.state()['status']['room']['queued'],1)
                bridge.close()
            requests=[]
            def online(request):
                requests.append(request)
                return httpx.Response(200,json={'alerts':[]})
            with httpx.Client(transport=httpx.MockTransport(online)) as client:
                bridge = RoomBridge(self.session,'http://room.test','token',db_path=file.name,client=client)
                bridge.tick()
                self.assertEqual(self.session.state()['status']['room']['queued'],0)
                body=requests[0].content.decode()
                self.assertIn('Builds FPGA tools',body)
                self.assertNotIn('PRIVATE RAW WORDS',body)
                self.assertNotIn('private@example.com',body)
                bridge.close()

    def test_alert_waits_for_capture_to_end_before_led_and_ack(self):
        calls=[]
        alert={'id':1,'kind':'intro','other':{'id':'sam','name':'Sam'},'reason':'Talk about FPGA'}
        def reply(request):
            calls.append(request.url.path)
            return httpx.Response(200,json={'alerts':[alert]} if request.method=='GET' else {})
        led=Mock(return_value=True)
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            bridge=RoomBridge(self.session,'http://room.test','token',alert=led,client=client)
            self.session.start()
            bridge.tick()
            led.assert_not_called()
            self.assertNotIn('/api/alerts/1/ack',calls)
            self.assertEqual(len(self.session.state()['status']['room']['alerts']),1)
            self.session.discard()
            bridge.tick()
            led.assert_called_once()
            self.assertIn('/api/alerts/1/ack',calls)
            bridge.tick()
            led.assert_called_once()
            bridge.close()

    def test_reset_clears_pending_encounters(self):
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'alerts':[]}))) as client:
            bridge=RoomBridge(self.session,'http://room.test','token',client=client)
            bridge.enqueue(self.person)
            bridge.reset()
            self.assertEqual(self.session.state()['status']['room']['queued'],0)
            bridge.close()
