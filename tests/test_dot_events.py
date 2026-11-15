import base64
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from remember.dot_events import Events, create_app, callback_address, signed_headers
from remember.store import Store
from remember.extract import HeuristicExtractor

class DotEventsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.source=Path(self.tmp.name)/'source.sqlite';self.state=Path(self.tmp.name)/'events.sqlite'
  self.store=Store(self.source);self.addCleanup(self.store.close)
  self.secret='whsec_'+base64.b64encode(b'x'*32).decode();self.token='t'*40;self.sent=[];self.code=200
  self.save('Old')
  self.events=Events(self.source,self.state,self.token,self.send);self.addCleanup(self.events.db.close)
  self.client=TestClient(create_app(self.events,run_worker=False))
 def save(self,name):
  return self.store.merge(HeuristicExtractor().extract("I'm "+name+". I build robots."),'PRIVATE TRANSCRIPT',[], '2026-11-14T12:00:00+00:00','2026-11-14T12:01:00+00:00')
 def send(self,url,body,headers):
  self.sent.append((url,json.loads(body),headers))
  payload=json.loads(body)
  if payload.get('type')=='verification':return 200,json.dumps({'challenge':payload['challenge']}).encode()
  return self.code,b'{}'
 def subscribe(self):
  return self.events.subscribe({'name':'conversation.completed','delivery':{'mode':'webhook','url':'https://receiver.example/callback','secret':self.secret}})
 def test_authentication_discovery_and_no_dashboard(self):
  body={'jsonrpc':'2.0','id':1,'method':'initialize'}
  self.assertEqual(self.client.post('/mcp',json=body).status_code,401)
  response=self.client.post('/mcp',headers={'Authorization':'Bearer '+self.token},json=body).json()
  self.assertIn('events',response['result']['capabilities'])
  response=self.client.post('/mcp/'+self.token,json=dict(body,method='events/list')).json()
  self.assertEqual(response['result']['events'][0]['name'],'conversation.completed')
  self.assertEqual(self.client.get('/api/state').status_code,404)
 def test_new_summary_delivered_once_and_no_history_or_transcript(self):
  self.subscribe();self.events.tick();self.assertEqual(len(self.sent),1)
  self.save('Raymond');self.events.tick();self.events.tick()
  self.assertEqual(len(self.sent),2)
  event=self.sent[-1][1];self.assertEqual(event['data']['contact_name'],'Raymond')
  self.assertNotIn('PRIVATE TRANSCRIPT',json.dumps(event))
  self.assertEqual(self.events.status()['deliveries'],{'delivered':1})
 def test_retry_retains_id_and_survives_restart(self):
  self.subscribe();self.save('Timothy');self.code=503;self.events.tick()
  identifier=self.sent[-1][1]['eventId']
  self.events.db.execute('UPDATE deliveries SET due=0');self.events.db.commit()
  restarted=Events(self.source,self.state,self.token,self.send);self.addCleanup(restarted.db.close)
  self.code=200;restarted.tick()
  self.assertEqual(self.sent[-1][1]['eventId'],identifier)
  self.assertEqual(restarted.status()['deliveries'],{'delivered':1})
 def test_unsubscribe_stops_deliveries(self):
  first=self.subscribe();self.assertEqual(first['id'],self.subscribe()['id'])
  self.events.unsubscribe({'name':'conversation.completed','delivery':{'url':'https://receiver.example/callback'}})
  self.save('Sam');self.events.tick();self.assertEqual(len(self.sent),2)
 def test_bad_challenge_and_secret_rejected(self):
  self.events.sender=lambda *args:(200,b'{"challenge":"wrong"}')
  with self.assertRaises(ValueError):self.subscribe()
  self.assertEqual(self.events.status()['active_subscriptions'],0)
 def test_expiry_and_gone_callback_stop_delivery(self):
  self.subscribe();self.code=410;self.save('Sam');self.events.tick()
  self.assertEqual(self.events.status()['active_subscriptions'],0)
  self.assertEqual(self.events.status()['deliveries'],{'failed':1})
 def test_signature_matches_standard_webhooks(self):
  body=b'{"hello":true}';h=signed_headers(self.secret,'sub','event',body,timestamp=123)
  expected=base64.b64encode(hmac.new(b'x'*32,b'event.123.'+body,hashlib.sha256).digest()).decode()
  self.assertEqual(h['webhook-signature'],'v1,'+expected)
 def test_private_callback_and_non_https_rejected(self):
  for url in ('http://example.com','https://user:pass@example.com','https://example.com:8443'):
   with self.assertRaises(ValueError):callback_address(url)
  with patch('remember.dot_events.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
   with self.assertRaises(ValueError):callback_address('https://example.com/callback')
 def test_token_rotation_revokes_old_subscriptions(self):
  self.subscribe()
  restarted=Events(self.source,self.state,'new-token-'+'x'*32,self.send);self.addCleanup(restarted.db.close)
  self.assertEqual(restarted.status()['active_subscriptions'],0)
 def test_deleted_conversation_cancels_queued_retry(self):
  self.subscribe();self.save('Sam');self.code=503;self.events.tick()
  self.events.db.execute('UPDATE deliveries SET due=0');self.events.db.commit()
  self.store.reset();before=len(self.sent);self.events.tick()
  self.assertEqual(len(self.sent),before)
  self.assertEqual(self.events.status()['deliveries'],{'cancelled':1})
