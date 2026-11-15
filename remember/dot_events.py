"""Dedicated, authenticated MCP Events sidecar for ChatGPT Dots.

New completed conversations and wearer-scoped room introductions are eligible. Does not
expose the dashboard, raw transcripts, credentials, or calendar write tools.
"""
import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

EVENT = 'conversation.completed'
SCHEMA = {'type': 'object', 'properties': {
    'conversation_id': {'type': 'string'}, 'contact_name': {'type': 'string'},
    'summary': {'type': 'string'}, 'ended_at': {'type': 'string'},
    'timezone': {'type': 'string'}, 'promises': {'type': 'array', 'items': {'type':'object'}}},
    'required': ['conversation_id','contact_name','summary','ended_at','timezone','promises'],
    'additionalProperties': False}

ROOM_EVENT = 'room.connection.created'
ROOM_SCHEMA = {'type':'object','properties':{
    'connection_id':{'type':'string'}, 'booking_key':{'type':'string'},
    'participants':{'type':'array','items':{'type':'object','properties':{
        'id':{'type':'string'},'name':{'type':'string'},'simulated':{'type':'boolean'}},
        'required':['id','name','simulated'],'additionalProperties':False}},
    'connection_types':{'type':'array','items':{'type':'string','enum':['intro','match']}},
    'reasons':{'type':'array','items':{'type':'string'}},
    'created_at':{'type':'string'},'timezone':{'type':'string'}},
    'required':['connection_id','booking_key','participants','connection_types','reasons','created_at','timezone'],
    'additionalProperties':False}



def utc(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value, timezone.utc).isoformat()


def signing_key(secret):
    if not isinstance(secret, str) or not secret.startswith('whsec_'):
        raise ValueError('Invalid signing secret')
    key = base64.b64decode(secret[6:], validate=True)
    if not 24 <= len(key) <= 64:
        raise ValueError('Invalid signing secret')
    return key


def signed_headers(secret, subscription_id, event_id, body, timestamp=None):
    stamp = str(int(time.time() if timestamp is None else timestamp))
    signature = base64.b64encode(hmac.new(signing_key(secret), event_id.encode()+b'.'+stamp.encode()+b'.'+body, hashlib.sha256).digest()).decode()
    return {'Content-Type':'application/json', 'webhook-id':event_id,
            'webhook-timestamp':stamp, 'webhook-signature':'v1,'+signature,
            'X-MCP-Subscription-Id':subscription_id}


def callback_address(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None,443) or parsed.fragment or any(ord(c)<33 for c in url)):
        raise ValueError('Callback must use public HTTPS')
    addresses = {record[4][0] for record in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in addresses):
        raise ValueError('Callback must use public HTTPS')
    return parsed, sorted(addresses)[0]


def send_callback(url, body, headers):
    """Validate DNS on every send and pin the socket; retain hostname for TLS."""
    parsed, address = callback_address(url)
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            raw = socket.create_connection((address,443), timeout=5)
            try:
                self.sock = self._context.wrap_socket(raw, server_hostname=parsed.hostname)
            except BaseException:
                raw.close()
                raise
    connection = PinnedHTTPS(parsed.hostname, timeout=5, context=ssl.create_default_context())
    try:
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        connection.request('POST',path,body=body,headers=headers)
        response = connection.getresponse()
        content = response.read(65537)
        if len(content)>65536:
            raise ValueError('Oversized callback response')
        return response.status, content
    finally:
        connection.close()


class Events:
    def __init__(self, source, db, token, sender=send_callback, *, room_source=None, participant_id=None):
        if len(token)<32:
            raise ValueError('Use a random MCP token of at least 32 characters')
        self.source,self.token,self.sender = str(source),token,sender
        self.room_source = str(room_source) if room_source else None
        self.participant_id = participant_id
        self.lock=threading.RLock()
        self.db=sqlite3.connect(db,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS bookings(booking_key TEXT PRIMARY KEY,status TEXT,event_id TEXT,meet_url TEXT);
        CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY,url TEXT,secret TEXT,expires REAL,active INTEGER);
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS deliveries(event_id TEXT,subscription_id TEXT,attempts INTEGER DEFAULT 0,due REAL,status TEXT DEFAULT 'pending',PRIMARY KEY(event_id,subscription_id));
        ''')
        if 'event_name' not in {r[1] for r in self.db.execute('PRAGMA table_info(subscriptions)')}:
            self.db.execute("ALTER TABLE subscriptions ADD COLUMN event_name TEXT NOT NULL DEFAULT 'conversation.completed'")
        if str(db)!=':memory:':os.chmod(db,0o600)
        fingerprint=hashlib.sha256(token.encode()).hexdigest()
        old=self.db.execute("SELECT value FROM metadata WHERE key='owner'").fetchone()
        if old and old[0]!=fingerprint:
            self.db.execute('UPDATE subscriptions SET active=0')
            self.db.execute("UPDATE deliveries SET status='cancelled' WHERE status='pending'")
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES('owner',?)",(fingerprint,))
        self.db.commit()
        # First installation starts with future conversations, not a history dump.
        with self.lock:
            if not self.db.execute('SELECT 1 FROM seen LIMIT 1').fetchone():
                for row in self.source_rows():
                    self.db.execute('INSERT OR IGNORE INTO seen VALUES(?)',(self.identity(row),))
                self.db.execute("INSERT OR IGNORE INTO seen VALUES('initialized')")
                self.db.commit()

        # Enabling room events never retroactively schedules existing edges.
        if self.room_source:
            marker='room_initialized:'+str(Path(self.room_source).resolve())+':'+str(self.participant_id)
            if not self.db.execute('SELECT 1 FROM metadata WHERE key=?',(marker,)).fetchone():
                for payload in self.room_payloads():
                    self.db.execute('INSERT OR IGNORE INTO seen VALUES(?)',(payload['eventId'],))
                self.db.execute('INSERT INTO metadata VALUES(?,?)',(marker,'1'))
                self.db.commit()

    def room_payloads(self):
        if not self.room_source or not self.participant_id:
            return []
        with sqlite3.connect(Path(self.room_source).resolve().as_uri()+'?mode=ro',uri=True) as conn:
            conn.row_factory=sqlite3.Row
            people={r['id']:json.loads(r['data']) for r in conn.execute('SELECT id,data FROM participants')}
            pairs={}
            for edge in conn.execute("SELECT * FROM edges WHERE kind IN ('intro','match') AND (source_id=? OR target_id=?) ORDER BY id",(self.participant_id,self.participant_id)):
                ids=tuple(sorted([edge['source_id'],edge['target_id']]))
                if any(i not in people or people[i].get('simulated',False) for i in ids):
                    continue
                pair=pairs.setdefault(ids,{'kinds':set(),'reasons':set(),'time':edge['created_at']})
                pair['kinds'].add(edge['kind'])
                for alert in conn.execute('SELECT payload FROM alerts WHERE edge_id=? AND participant_id=?',(edge['id'],self.participant_id)):
                    reason=json.loads(alert[0]).get('reason','')
                    if reason:pair['reasons'].add(str(reason)[:1000])
            result=[]
            for ids,pair in pairs.items():
                key=hashlib.sha256(json.dumps([str(Path(self.room_source).resolve()),ids]).encode()).hexdigest()
                event_id='room_'+key
                result.append({'eventId':event_id,'name':ROOM_EVENT,'timestamp':utc(pair['time']),
                    'data':{'connection_id':key,'booking_key':'afterhello-room-'+key,
                            'participants':[{'id':i,'name':people[i]['name'],'simulated':False} for i in ids],
                            'connection_types':sorted(pair['kinds']),'reasons':sorted(pair['reasons']),
                            'created_at':utc(pair['time']),'timezone':os.getenv('AFTERHELLO_TIMEZONE','America/Los_Angeles')},'cursor':None})
            return result

    def claim_booking(self, key):
        if not isinstance(key,str):raise ValueError('Invalid booking key')
        with self.lock:
            exists=any(json.loads(r[0]).get('data',{}).get('booking_key')==key
                       for r in self.db.execute('SELECT payload FROM events'))
            if not exists:raise ValueError('Unknown room scheduling request')
            inserted=self.db.execute("INSERT OR IGNORE INTO bookings VALUES(?,'claimed','','')",(key,)).rowcount
            row=dict(self.db.execute('SELECT * FROM bookings WHERE booking_key=?',(key,)).fetchone())
            self.db.commit()
            return dict(row,granted=bool(inserted))

    def complete_booking(self,key,event_id,meet_url):
        if not isinstance(event_id,str) or not 1<=len(event_id)<=512:raise ValueError('Invalid calendar event ID')
        parsed=urlsplit(meet_url)
        if parsed.scheme!='https' or parsed.hostname!='meet.google.com' or parsed.username or parsed.password:
            raise ValueError('Use the actual Google Meet URL')
        with self.lock:
            row=self.db.execute('SELECT * FROM bookings WHERE booking_key=?',(key,)).fetchone()
            if not row:raise ValueError('Claim this booking first')
            if row['status']=='booked' and (row['event_id']!=event_id or row['meet_url']!=meet_url):
                raise ValueError('A different booking is already recorded')
            self.db.execute("UPDATE bookings SET status='booked',event_id=?,meet_url=? WHERE booking_key=?",(event_id,meet_url,key))
            self.db.commit()
            return {'status':'booked','event_id':event_id,'meet_url':meet_url}

    def source_rows(self):
        with sqlite3.connect(Path(self.source).resolve().as_uri()+'?mode=ro',uri=True) as conn:
            conn.row_factory=sqlite3.Row
            rows=conn.execute('SELECT c.id,c.summary,c.ended_at,p.name,p.contact_uid FROM conversations c JOIN people p ON c.person_id=p.id ORDER BY c.id').fetchall()
            return [dict(row, promises=[{'what':p[0],'by_when':p[1]} for p in conn.execute('SELECT what,by_when FROM promises WHERE conversation_id=?',(row['id'],))]) for row in rows]

    @staticmethod
    def identity(row):
        return hashlib.sha256(('%s:%s:%s'%(row['contact_uid'],row['id'],row['ended_at'])).encode()).hexdigest()

    def subscribe(self, params):
        if params.get('name') not in (EVENT,ROOM_EVENT) or params.get('arguments',{})!={}:
            raise ValueError('Unknown event or unsupported filters')
        if params.get('cursor') is not None:
            raise ValueError('Replay cursors are not supported')
        delivery=params.get('delivery',{})
        if delivery.get('mode')!='webhook':raise ValueError('Webhook delivery required')
        url,secret=delivery['url'],delivery['secret']
        signing_key(secret)
        identifier=hashlib.sha256((hashlib.sha256(self.token.encode()).hexdigest()+params['name']+url).encode()).hexdigest()
        ttl=params.get('ttlMs',86400000)
        if ttl is None:ttl=86400000
        if type(ttl) not in (int,float) or not 1000<=ttl<=604800000:raise ValueError('TTL must be between one second and seven days')
        challenge=secrets.token_urlsafe(32)
        body=json.dumps({'type':'verification','challenge':challenge}).encode()
        status,content=self.sender(url,body,signed_headers(secret,identifier,'verify_'+secrets.token_hex(12),body))
        if not 200<=status<300 or not hmac.compare_digest(str(json.loads(content).get('challenge','')),challenge):
            raise ValueError('Callback challenge failed')
        expires=time.time()+ttl/1000
        with self.lock:
            self.db.execute('INSERT INTO subscriptions(id,url,secret,expires,active,event_name) VALUES(?,?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET secret=excluded.secret,expires=excluded.expires,active=1',(identifier,url,secret,expires,params['name']))
            self.db.commit()
        return {'id':identifier,'refreshBefore':utc(expires),'cursor':None,'truncated':False}

    def unsubscribe(self,params):
        if params.get('name') not in (EVENT,ROOM_EVENT) or params.get('arguments',{})!={}:raise ValueError('Unknown subscription')
        with self.lock:
            self.db.execute('UPDATE subscriptions SET active=0 WHERE url=? AND event_name=?',(params.get('delivery',{}).get('url',''),params['name']))
            self.db.commit()
        return {}

    def tick(self):
        rows=self.source_rows()
        room_payloads=self.room_payloads()
        with self.lock:
            now=time.time()
            active=self.db.execute('SELECT id FROM subscriptions WHERE active=1 AND expires>? AND event_name=?',(now,EVENT)).fetchall()
            for row in rows:
                key=self.identity(row)
                if self.db.execute('SELECT 1 FROM seen WHERE id=?',(key,)).fetchone():continue
                self.db.execute('INSERT INTO seen VALUES(?)',(key,))
                if not active:continue
                payload={'eventId':'conversation_'+key,'name':EVENT,'timestamp':utc(), 'data':{
                    'conversation_id':key,'contact_name':row['name'],'summary':row['summary'][:12000],
                    'ended_at':row['ended_at'],'timezone':os.getenv('AFTERHELLO_TIMEZONE','America/Los_Angeles'),
                    'promises':row['promises'][:8]},'cursor':None}
                body=json.dumps(payload,ensure_ascii=False)
                self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?)',(payload['eventId'],body))
                for sub in active:self.db.execute('INSERT OR IGNORE INTO deliveries(event_id,subscription_id,due) VALUES(?,?,?)',(payload['eventId'],sub['id'],now))
            room_subs=self.db.execute('SELECT id FROM subscriptions WHERE active=1 AND expires>? AND event_name=?',(now,ROOM_EVENT)).fetchall()
            for payload in room_payloads:
                key=payload['eventId']
                if self.db.execute('SELECT 1 FROM seen WHERE id=?',(key,)).fetchone():continue
                self.db.execute('INSERT INTO seen VALUES(?)',(key,))
                if not room_subs:continue
                self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?)',(key,json.dumps(payload,ensure_ascii=False)))
                for sub in room_subs:
                    self.db.execute('INSERT OR IGNORE INTO deliveries(event_id,subscription_id,due) VALUES(?,?,?)',(key,sub['id'],now))
            self.db.commit()
            jobs=self.db.execute('''SELECT d.*,s.url,s.secret,e.payload FROM deliveries d JOIN subscriptions s ON d.subscription_id=s.id JOIN events e ON d.event_id=e.id WHERE d.status='pending' AND d.due<=? AND s.active=1 AND s.expires>? LIMIT 10''',(now,now)).fetchall()
        current_keys={'conversation_'+self.identity(row) for row in rows} | {p['eventId'] for p in room_payloads}
        for job in jobs:
            with self.lock:
                valid=self.db.execute('SELECT * FROM subscriptions WHERE id=? AND active=1 AND expires>?',(job['subscription_id'],time.time())).fetchone()
                if not valid or job['event_id'] not in current_keys:
                    self.db.execute("UPDATE deliveries SET status='cancelled' WHERE event_id=? AND subscription_id=?",(job['event_id'],job['subscription_id']))
                    self.db.commit()
                    continue
            body=job['payload'].encode()
            try:
                status,_=self.sender(job['url'],body,signed_headers(valid['secret'],job['subscription_id'],job['event_id'],body))
            except Exception:status=0
            attempts=job['attempts']+1
            outcome='delivered' if 200<=status<300 else 'failed' if status in (410,413) or attempts>=5 else 'pending'
            with self.lock:
                self.db.execute('UPDATE deliveries SET status=?,attempts=?,due=? WHERE event_id=? AND subscription_id=?',(outcome,attempts,time.time()+min(300,2**attempts),job['event_id'],job['subscription_id']))
                if status==410:self.db.execute('UPDATE subscriptions SET active=0 WHERE id=?',(job['subscription_id'],))
                self.db.commit()

    def status(self):
        with self.lock:
            return {'active_subscriptions':self.db.execute('SELECT COUNT(*) FROM subscriptions WHERE active=1 AND expires>?',(time.time(),)).fetchone()[0],
                    'subscriptions_by_event':{r[0]:r[1] for r in self.db.execute('SELECT event_name,COUNT(*) FROM subscriptions WHERE active=1 AND expires>? GROUP BY event_name',(time.time(),))},
                    'room_source_configured':bool(self.room_source and self.participant_id),
                    'bookings':{r[0]:r[1] for r in self.db.execute('SELECT status,COUNT(*) FROM bookings GROUP BY status')},
                    'deliveries':{r[0]:r[1] for r in self.db.execute('SELECT status,COUNT(*) FROM deliveries GROUP BY status')}}


def create_app(events, run_worker=True):
    @asynccontextmanager
    async def lifespan(app):
        async def worker():
            while True:
                try:await asyncio.to_thread(events.tick)
                except Exception:print('AfterHello event polling will retry.',flush=True)
                await asyncio.sleep(2)
        task=asyncio.create_task(worker()) if run_worker else None
        yield
        if task:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
    app=FastAPI(title='AfterHello MCP',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

    @app.get('/health')
    def health():return {'service':'AfterHello MCP','ok':True}

    @app.post('/mcp')
    @app.post('/mcp/{capability}')
    async def rpc(request:Request,capability:str=''):
        supplied=request.headers.get('authorization','').removeprefix('Bearer ') or capability
        if not hmac.compare_digest(supplied.encode(),events.token.encode()):return JSONResponse({'error':'Unauthorized'},status_code=401)
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>65536:return Response(status_code=413)
        try:message=json.loads(raw)
        except ValueError:return JSONResponse({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid JSON'}})
        if not isinstance(message,dict):return JSONResponse({'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid request'}})
        ident=message.get('id');method=message.get('method');params=message.get('params') or {}
        if method=='notifications/initialized':return Response(status_code=202)
        try:
            if method=='initialize':result={'protocolVersion':'2025-03-26','capabilities':{'tools':{},'events':{}},'serverInfo':{'name':'AfterHello','version':'1.0.0'}}
            elif method=='ping':result={}
            elif method=='tools/list':result={'tools':[{'name':'afterhello_status','description':'Check AfterHello subscriptions by event type, event delivery counts, and calendar booking states.','inputSchema':{'type':'object','properties':{},'additionalProperties':False},'annotations':{'readOnlyHint':True}},
                {'name':'claim_room_booking','description':'Atomically claim a room booking key before creating a calendar event. Only create if granted is true. A previously claimed request needs reconciliation, not another event.','inputSchema':{'type':'object','properties':{'booking_key':{'type':'string'}},'required':['booking_key'],'additionalProperties':False},'annotations':{'readOnlyHint':False,'idempotentHint':True}},
                {'name':'complete_room_booking','description':'Record the calendar event ID and Google Meet URL after successful booking.','inputSchema':{'type':'object','properties':{'booking_key':{'type':'string'},'event_id':{'type':'string'},'meet_url':{'type':'string'}},'required':['booking_key','event_id','meet_url'],'additionalProperties':False},'annotations':{'readOnlyHint':False,'idempotentHint':True}}]}
            elif method=='tools/call' and params.get('name')=='afterhello_status':result={'content':[{'type':'text','text':json.dumps(events.status())}]}
            elif method=='tools/call' and params.get('name')=='claim_room_booking':result={'content':[{'type':'text','text':json.dumps(events.claim_booking(params.get('arguments',{}).get('booking_key')))}]}
            elif method=='tools/call' and params.get('name')=='complete_room_booking':
                a=params.get('arguments',{});result={'content':[{'type':'text','text':json.dumps(events.complete_booking(a.get('booking_key'),a.get('event_id'),a.get('meet_url')))}]}
            elif method=='events/list':result={'events':[{'name':EVENT,'description':'A newly saved AfterHello conversation summary and wearer promises, for follow-up planning.','delivery':['webhook'],'inputSchema':{'type':'object','properties':{},'additionalProperties':False},'payloadSchema':SCHEMA}, {'name':ROOM_EVENT,'description':'A new introduction or shared-field recommendation involving the configured wearer and another real participant; one scheduling opportunity per pair.','delivery':['webhook'],'inputSchema':{'type':'object','properties':{},'additionalProperties':False},'payloadSchema':ROOM_SCHEMA}]}
            elif method=='events/subscribe':result=await asyncio.to_thread(events.subscribe,params)
            elif method=='events/unsubscribe':result=events.unsubscribe(params)
            else:return JSONResponse({'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'Method not found'}})
            return JSONResponse({'jsonrpc':'2.0','id':ident,'result':result})
        except Exception:
            return JSONResponse({'jsonrpc':'2.0','id':ident,'error':{'code':-32015 if method=='events/subscribe' else -32602,'message':'Callback verification or subscription parameters invalid.' if method=='events/subscribe' else 'Invalid parameters or conflicting booking state.','data':{'reason':'challenge_failed'}}})
    return app


def main():
    from dotenv import load_dotenv
    import uvicorn
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default='remember.sqlite')
    parser.add_argument('--db',default='afterhello-events.sqlite')
    parser.add_argument('--port',type=int,default=8010)
    parser.add_argument('--room-source',default=None)
    parser.add_argument('--participant-id',default=None)
    args=parser.parse_args();load_dotenv('.env')
    events=Events(args.source,args.db,os.environ.get('AFTERHELLO_MCP_TOKEN',''),
                  room_source=args.room_source or os.getenv('AFTERHELLO_ROOM_SOURCE'),
                  participant_id=args.participant_id or os.getenv('AFTERHELLO_ROOM_PARTICIPANT_ID'))
    uvicorn.run(create_app(events),host='127.0.0.1',port=args.port,access_log=False,log_level='warning')

if __name__=='__main__':main()
