import unittest
from remember.room_matching import match_room
from remember.room import Room

PEOPLE = [
 {'id':'timothy','name':'Timothy Do','working_on':'Making physical AI inference better on the edge'},
 {'id':'raymond','name':'Raymond Mak','working_on':'Firmware and RISC-V cores'},
 {'id':'priya','name':'Priya Shah','role':'Product designer'},
]
class FieldTests(unittest.TestCase):
 def test_raymond_intro_recommends_timothy_from_saved_profiles(self):
  matches=match_room(PEOPLE,'timothy',{'name':'Raymond'},use_model=False)
  self.assertEqual(len(matches),1)
  self.assertEqual({matches[0]['source_id'],matches[0]['target_id']},{'raymond','timothy'})
  self.assertEqual(matches[0]['kind'],'match')
  self.assertIn('hardware and embedded',matches[0]['reason'])
 def test_no_intro_or_ambiguous_identity_cannot_trigger_field_match(self):
  self.assertEqual(match_room(PEOPLE,'timothy',{},use_model=False),[])
  self.assertEqual(match_room(PEOPLE+[{'id':'r2','name':'Raymond Lee'}],'timothy',{'name':'Raymond'},use_model=False),[])
 def test_generic_or_negated_fields_do_not_match(self):
  for role in ('Interested in tech','Not interested in firmware'):
   people=[PEOPLE[0],dict(PEOPLE[1],working_on=role)]
   self.assertEqual(match_room(people,'timothy',{'name':'Raymond'},use_model=False),[])
 def test_persisted_roster_match_alerts_and_duplicate_encounter(self):
  room=Room(':memory:');self.addCleanup(room.close)
  for p in PEOPLE:room.register(p)
  event={'event_id':'intro-1','card':{'name':'Raymond'}}
  result=room.encounter('timothy',event)
  self.assertEqual({e['kind'] for e in result['edges']},{'meeting','match'})
  self.assertEqual(len(room.alerts('timothy')['alerts']),1)
  self.assertEqual(len(room.alerts('raymond')['alerts']),1)
  self.assertEqual(room.alerts('priya')['alerts'],[])
  self.assertTrue(room.encounter('timothy',event)['duplicate'])
