"""Shared room coordination: private participant alerts and a public-safe room map."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field


class ParticipantIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=150)
    company: str = Field(default='', max_length=200)
    role: str = Field(default='', max_length=200)
    needs: list[str] = Field(default_factory=list, max_length=20)
    offers: list[str] = Field(default_factory=list, max_length=20)
    simulated: bool = False


class CardIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='', max_length=150)
    company: str = Field(default='', max_length=200)
    role: str = Field(default='', max_length=200)
    summary: str = Field(default='', max_length=3000)
    working_on: str = Field(default='', max_length=1000)


class EncounterIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str = Field(min_length=1, max_length=128)
    other_id: str | None = Field(default=None, max_length=64)
    card: CardIn = Field(default_factory=CardIn)
    marked: bool = False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Room:
    def __init__(self, db_path=':memory:', matcher=None):
        if matcher is None:
            from .room_matching import match_room
            matcher = match_room
        self.matcher = matcher
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(2)
        self.inflight = set()
        self.generation = 0
        self.revision = 0
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS participants (
              id TEXT PRIMARY KEY, data TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS encounters (
              participant_id TEXT NOT NULL, event_id TEXT NOT NULL,
              PRIMARY KEY(participant_id,event_id));
            CREATE TABLE IF NOT EXISTS edges (
              id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
              kind TEXT NOT NULL, created_at REAL NOT NULL,
              UNIQUE(source_id,target_id,kind));
            CREATE TABLE IF NOT EXISTS alerts (
              id INTEGER PRIMARY KEY, participant_id TEXT NOT NULL,
              edge_id INTEGER NOT NULL, payload TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
              UNIQUE(participant_id,edge_id));
        ''')
        self.conn.commit()

    def close(self):
        with self.lock:
            self.conn.close()

    def participants(self):
        with self.lock:
            return [json.loads(row['data']) for row in self.conn.execute('SELECT data FROM participants ORDER BY id')]

    def register(self, participant):
        body = ParticipantIn.model_validate(participant).model_dump()
        body['name'] = body['name'].strip()
        if not body['name'] or any(len(item) > 500 for item in body['needs'] + body['offers']):
            raise HTTPException(400, 'Provide a name and short needs/offers.')
        token = secrets.token_urlsafe(32)
        with self.lock:
            exists = self.conn.execute('SELECT 1 FROM participants WHERE id=?', (body['id'],)).fetchone()
            if not exists and self.conn.execute('SELECT COUNT(*) FROM participants').fetchone()[0] >= 100:
                raise HTTPException(409, 'This room is full (100 participants).')
            self.conn.execute('INSERT INTO participants VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, token_hash=excluded.token_hash',
                              (body['id'], json.dumps(body), token_hash(token)))
            self.conn.commit()
            self.generation += 1
            self.revision += 1
        return {'participant': body, 'token': token}

    def authenticate_participant(self, token):
        with self.lock:
            row = self.conn.execute('SELECT id FROM participants WHERE token_hash=?', (token_hash(token),)).fetchone()
        return row['id'] if row else None

    def state(self):
        with self.lock:
            return {'participants': [{key: p[key] for key in ('id', 'name', 'simulated')} for p in self.participants()],
                    'edges': [dict(row) for row in self.conn.execute('SELECT * FROM edges ORDER BY id')],
                    'revision': self.revision}

    def reset(self):
        with self.lock:
            self.generation += 1
            for table in ('alerts', 'edges', 'encounters', 'participants'):
                self.conn.execute('DELETE FROM ' + table)
            self.conn.commit()
            self.revision += 1
        return self.state()

    def _add_edge(self, source, target, kind, reason, participants):
        if source == target or source not in participants or target not in participants or kind not in ('meeting', 'intro', 'match'):
            return None
        # Edges represent pairs; sorting prevents duplicate reverse matches.
        source, target = sorted((source, target))
        cur = self.conn.execute('INSERT OR IGNORE INTO edges(source_id,target_id,kind,created_at) VALUES(?,?,?,?)',
                                (source, target, kind, time.time()))
        if not cur.rowcount:
            return None
        edge_id = cur.lastrowid
        if kind != 'meeting':
            for recipient, other in ((source, target), (target, source)):
                peer = participants[other]
                payload = {'id': edge_id, 'kind': kind, 'other': {key: peer.get(key, '') for key in ('id', 'name', 'company', 'role')},
                           'reason': str(reason)[:1000], 'created_at': time.time()}
                self.conn.execute('INSERT INTO alerts(participant_id,edge_id,payload) VALUES(?,?,?)',
                                  (recipient, edge_id, json.dumps(payload)))
        return dict(self.conn.execute('SELECT * FROM edges WHERE id=?', (edge_id,)).fetchone())

    def encounter(self, participant_id, body, *, matcher=None):
        body = EncounterIn.model_validate(body)
        identity = (participant_id, body.event_id)
        with self.lock:
            participants = {p['id']: p for p in self.participants()}
            if participant_id not in participants:
                raise HTTPException(401, 'Participant registration expired.')
            if body.other_id and (body.other_id not in participants or body.other_id == participant_id):
                raise HTTPException(400, 'Choose a different registered participant.')
            if self.conn.execute('SELECT 1 FROM encounters WHERE participant_id=? AND event_id=?', identity).fetchone():
                return {'accepted': True, 'duplicate': True, 'edges': []}
            if identity in self.inflight or not self.slots.acquire(blocking=False):
                raise HTTPException(409, 'Room is processing. Retry this event shortly.')
            self.inflight.add(identity)
            generation = self.generation
            other_id = body.other_id
            if other_id is None and body.card.name.strip():
                name = ' '.join(body.card.name.casefold().split())
                candidates = [p['id'] for p in participants.values() if p['id'] != participant_id
                              and ' '.join(p['name'].casefold().split()) == name]
                if len(name.split()) == 1:
                    candidates = [p['id'] for p in participants.values() if p['id'] != participant_id
                                  and p['name'].casefold().split()[0] == name]
                if len(candidates) == 1:
                    other_id = candidates[0]
        try:
            from .room_matching import encounter_profile_updates
            profile_updates = encounter_profile_updates(list(participants.values()), body.card.model_dump())
            # No room lock is held during a potentially slow model call.
            matches = (matcher or self.matcher)(list(participants.values()), participant_id, body.card.model_dump(), marked=body.marked)
            with self.lock:
                if generation != self.generation:
                    raise HTTPException(409, 'Room was reset. Register again.')
                for update in profile_updates:
                    row = self.conn.execute('SELECT data FROM participants WHERE id=?', (update['id'],)).fetchone()
                    if row:
                        profile = json.loads(row['data'])
                        for field in ('needs', 'offers'):
                            profile[field] = list(dict.fromkeys(profile.get(field, []) + update.get(field, [])))[:20]
                        self.conn.execute('UPDATE participants SET data=? WHERE id=?', (json.dumps(profile), update['id']))
                edges = []
                if other_id:
                    edge = self._add_edge(participant_id, other_id, 'meeting', '', participants)
                    if edge:
                        edges.append(edge)
                for match in (matches or [])[:50]:
                    if (not isinstance(match, dict) or match.get('kind') not in ('intro', 'match')
                            or not any(person_id in (match.get('source_id'), match.get('target_id'))
                                       for person_id in (participant_id, other_id) if person_id)):
                        continue
                    edge = self._add_edge(match.get('source_id'), match.get('target_id'), match.get('kind'), match.get('reason', ''), participants)
                    if edge:
                        edges.append(edge)
                self.conn.execute('INSERT INTO encounters VALUES(?,?)', identity)
                self.conn.commit()
                self.revision += 1
                return {'accepted': True, 'duplicate': False, 'edges': edges}
        except HTTPException:
            raise
        except Exception:
            with self.lock:
                self.conn.rollback()
            raise HTTPException(503, 'Room matching could not finish. Retry this event.') from None
        finally:
            with self.lock:
                self.inflight.discard(identity)
            self.slots.release()

    def alerts(self, participant_id):
        with self.lock:
            rows = self.conn.execute('SELECT id,payload FROM alerts WHERE participant_id=? AND acknowledged=0 ORDER BY id LIMIT 100', (participant_id,)).fetchall()
            return {'participant_id': participant_id,
                    'alerts': [dict(json.loads(row['payload']), id=row['id']) for row in rows]}

    def ack(self, participant_id, alert_id):
        with self.lock:
            cur = self.conn.execute('UPDATE alerts SET acknowledged=1 WHERE id=? AND participant_id=?', (alert_id, participant_id))
            self.conn.commit()
            if not cur.rowcount:
                raise HTTPException(404, 'Alert not found.')
        return {'acknowledged': True}

    def demo(self, reset=False):
        if reset:
            self.reset()
        known = {p['id'] for p in self.participants()}
        tokens = {}
        sample = [
            {'id': 'alex', 'name': 'Alex Chen', 'role': 'Engineering student', 'needs': ['FPGA internship'], 'offers': [], 'simulated': True},
            {'id': 'priya', 'name': 'Priya Shah', 'company': 'Circuit Works', 'role': 'Engineer', 'needs': [], 'offers': [], 'simulated': True},
            {'id': 'sam', 'name': 'Sam Rivera', 'company': 'Circuit Works', 'role': 'FPGA team lead', 'needs': [], 'offers': ['Hiring FPGA interns'], 'simulated': True},
        ]
        if any(p['id'] in known for p in sample):
            raise HTTPException(409, 'Demo participant IDs already exist. Reset the demo to replay.')
        for p in sample:
            tokens[p['id']] = self.register(p)['token']
        from functools import partial
        from .room_matching import match_room
        result = self.encounter('alex', {'event_id': 'demo-intro', 'other_id': 'priya', 'marked': True,
                                        'card': {'name': 'Priya Shah', 'company': 'Circuit Works', 'summary': 'Priya says: You should meet Sam Rivera, he is hiring FPGA interns. You two should talk about RISC-V.', 'working_on': 'FPGA and RISC-V'}}, matcher=partial(match_room, use_model=False))
        return {'state': self.state(), 'participant_tokens': tokens, 'encounter': result}


def create_room_app(db_path=':memory:', *, admin_token=None, projector_token=None, matcher=None):
    room = Room(db_path, matcher)
    admin_token = admin_token or secrets.token_urlsafe(32)
    projector_token = projector_token or secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        room.close()

    app = FastAPI(title='AfterHello room', lifespan=lifespan)
    app.state.room = room
    app.state.admin_token = admin_token
    app.state.projector_token = projector_token

    @app.middleware('http')
    async def auth(request: Request, call_next):
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host', ''):
            return JSONResponse({'detail': 'Cross-origin requests are not accepted.'}, status_code=403)
        token = request.headers.get('authorization', '').removeprefix('Bearer ')
        if request.method == 'GET' and request.url.path in ('/', '/api/events'):
            token = token or request.query_params.get('token', '')
        is_admin = secrets.compare_digest(token.encode(), admin_token.encode())
        is_projector = secrets.compare_digest(token.encode(), projector_token.encode())
        participant = room.authenticate_participant(token) if token else None
        path = request.url.path
        allowed = (path == '/' or path in ('/api/state', '/api/events')) and request.method == 'GET' and (is_admin or is_projector)
        allowed = allowed or (path in ('/api/participants', '/api/demo', '/api/reset') and request.method == 'POST' and is_admin)
        allowed = allowed or (participant is not None and (path == '/api/encounters' or path.startswith('/api/alerts')))
        if not allowed:
            return JSONResponse({'detail': 'Use the room link or your participant token.'}, status_code=401)
        request.state.participant_id = participant
        request.state.is_admin = is_admin
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.get('/')
    def index():
        return FileResponse(Path(__file__).with_name('static') / 'room.html')

    @app.get('/api/state')
    def state(request: Request):
        return dict(room.state(), can_control=request.state.is_admin)

    @app.post('/api/participants')
    def register(body: ParticipantIn):
        return room.register(body.model_dump())

    @app.post('/api/encounters')
    def encounter(body: EncounterIn, request: Request):
        return room.encounter(request.state.participant_id, body)

    @app.get('/api/alerts')
    def alerts(request: Request):
        return room.alerts(request.state.participant_id)

    @app.post('/api/alerts/{alert_id}/ack')
    def ack(alert_id: int, request: Request):
        return room.ack(request.state.participant_id, alert_id)

    @app.post('/api/reset')
    def reset():
        return dict(room.reset(), can_control=True)

    @app.post('/api/demo')
    def demo(body: dict | None = None):
        result = room.demo(reset=(body or {}).get('reset') is True)
        result['state']['can_control'] = True
        return result

    @app.get('/api/events')
    async def events(request: Request):
        async def generate():
            revision = -1
            keepalive = time.monotonic()
            while not await request.is_disconnected():
                state = room.state()
                if state['revision'] != revision:
                    revision = state['revision']
                    yield 'data: ' + json.dumps({'type': 'state', **state, 'can_control': request.state.is_admin}) + '\n\n'
                elif time.monotonic() - keepalive > 15:
                    yield ': keepalive\n\n'
                    keepalive = time.monotonic()
                await asyncio.sleep(0.25)
        return StreamingResponse(generate(), media_type='text/event-stream')

    return app


def main():
    parser = argparse.ArgumentParser(description='AfterHello shared room server')
    parser.add_argument('--db', default='remember-room.sqlite')
    parser.add_argument('--port', type=int, default=8003)
    parser.add_argument('--lan', action='store_true')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    from .kitpath import load_dotenv
    load_dotenv()
    app = create_room_app(args.db)
    if args.demo:
        # Demo uses deterministic local matching even when API keys are configured.
        result = app.state.room.demo(reset=False)
        print('Simulated participant tokens: ' + json.dumps(result['participant_tokens']))
    print('Admin: http://127.0.0.1:%d/?token=%s' % (args.port, app.state.admin_token))
    print('Projector: http://127.0.0.1:%d/?token=%s' % (args.port, app.state.projector_token))
    import uvicorn
    uvicorn.run(app, host='0.0.0.0' if args.lan else '127.0.0.1', port=args.port, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
