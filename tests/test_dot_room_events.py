import base64
import json
from pathlib import Path
import tempfile
import threading
import unittest
from remember.dot_events import Events, ROOM_EVENT, EVENT
from remember.room import Room
from remember.store import Store

class DotRoomTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.path=Path(self.temp.name);self.store=Store(self.path/'contacts.sqlite');self.addCleanup(self.store.close)
  self.room=Room(self.path/'room.sqlite');self.addCleanup(self.room.close)
  for p in [{'id':'tim','name':'Timothy Do'},{'id':'ray','name':'Raymond Mak'},{'id':'other','name':'Other Person'},{'id':'sim','name':'Sim Person','simulated':True}]:self.room.register(p)
  self.sent=[];self.code=200
  self.events=Events(self.path/'contacts.sqlite',self.path/'events.sqlite','x'*40,self.send,room_source=self.path/'room.sqlite',participant_id='tim');self.addCleanup(self.events.db.close)
 def send(self,url,body,headers):
  data=json.loads(body);self.sent.append(data)
  return (200,json.dumps({'challenge':data['challenge']}).encode()) if data.get('type')=='verification' else (self.code,b'{}')
 def subscribe(self,name=ROOM_EVENT):
  return self.events.subscribe({'name':name,'delivery':{'url':'https://receiver.example/hook','mode':'webhook','secret':'whsec_'+base64.b64encode(b's'*32).decode()}})
 def edge(self,a='tim',b='ray',kind='intro'):
  with self.room.lock:
   self.room._add_edge(a,b,kind,'They share embedded systems.',{p['id']:p for p in self.room.participants()});self.room.conn.commit()
 def payloads(self):return [p for p in self.sent if p.get('name')==ROOM_EVENT]
 def test_intro_and_match_for_pair_emit_once_even_across_restart(self):
  self.subscribe();self.edge();self.edge(kind='match');self.events.tick()
  self.assertEqual(len(self.payloads()),1)
  self.assertEqual(self.payloads()[0]['data']['connection_types'],['intro','match'])
  restarted=Events(self.path/'contacts.sqlite',self.path/'events.sqlite','x'*40,self.send,room_source=self.path/'room.sqlite',participant_id='tim');self.addCleanup(restarted.db.close)
  restarted.tick();self.assertEqual(len(self.payloads()),1)
 def test_later_match_does_not_emit_second_request(self):
  self.subscribe();self.edge();self.events.tick();self.edge(kind='match');self.events.tick();self.assertEqual(len(self.payloads()),1)
 def test_meetings_simulated_and_unrelated_pairs_do_not_trigger(self):
  self.subscribe();self.edge(kind='meeting');self.edge(b='sim');self.edge(a='ray',b='other');self.events.tick();self.assertEqual(self.payloads(),[])
 def test_old_conversation_subscription_does_not_receive_room_event(self):
  self.subscribe(EVENT);self.edge();self.events.tick();self.assertEqual(self.payloads(),[])
 def test_unsubscribe_is_scoped_to_event_name(self):
  self.subscribe(EVENT);self.subscribe();self.events.unsubscribe({'name':EVENT,'delivery':{'url':'https://receiver.example/hook'}})
  self.edge();self.events.tick();self.assertEqual(len(self.payloads()),1)
 def test_no_backfill_on_first_room_enable(self):
  self.edge()
  fresh=Events(self.path/'contacts.sqlite',self.path/'fresh.sqlite','x'*40,self.send,room_source=self.path/'room.sqlite',participant_id='tim');self.addCleanup(fresh.db.close)
  fresh.subscribe({'name':ROOM_EVENT,'delivery':{'url':'https://receiver.example/hook','mode':'webhook','secret':'whsec_'+base64.b64encode(b's'*32).decode()}})
  fresh.tick();self.assertEqual(self.payloads(),[])
 def test_claim_is_atomic_and_completed_booking_is_idempotent(self):
  self.subscribe();self.edge();self.events.tick();key=self.payloads()[0]['data']['booking_key'];results=[]
  threads=[threading.Thread(target=lambda:results.append(self.events.claim_booking(key))) for _ in range(8)]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertEqual(sum(r['granted'] for r in results),1)
  result=self.events.complete_booking(key,'calendar-event','https://meet.google.com/abc-defg-hij')
  self.assertEqual(result,self.events.complete_booking(key,'calendar-event','https://meet.google.com/abc-defg-hij'))
  self.assertFalse(self.events.claim_booking(key)['granted'])
  with self.assertRaises(ValueError):self.events.complete_booking(key,'different','https://meet.google.com/abc-defg-hij')
  with self.assertRaises(ValueError):self.events.claim_booking('unknown')
 def test_deleted_pair_cancels_queued_delivery(self):
  self.subscribe();self.edge();self.code=503;self.events.tick()
  with self.room.lock:self.room.conn.execute('DELETE FROM edges');self.room.conn.commit()
  self.events.db.execute('UPDATE deliveries SET due=0');self.events.db.commit();count=len(self.payloads());self.events.tick()
  self.assertEqual(len(self.payloads()),count)
  self.assertEqual(self.events.status()['deliveries'],{'cancelled':1})
