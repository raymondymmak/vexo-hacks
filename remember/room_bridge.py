"""Opt-in participant bridge. Queued summaries survive outages; raw dialogue stays local."""
import json
import hashlib
import sqlite3
import threading
from urllib.parse import urlsplit

import httpx


class RoomBridge:
    def __init__(self, session, url, token, *, db_path=':memory:', alert=None, client=None):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Room URL must be an HTTP(S) server address without credentials.')
        self.session, self.url, self.token = session, url.rstrip('/'), token
        self.scope = hashlib.sha256((self.url + "|" + token).encode()).hexdigest()[:16] + ":"
        self.alert = alert or (lambda item: True)
        self.client = client or httpx.Client(timeout=3, follow_redirects=False, trust_env=False)
        self._owns_client = client is None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._generation = 0
        self._seen = {}
        self._lit = set()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS room_outbox (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.commit()
        self._status('connecting', 'Connecting to the room.')

    def _status(self, state, detail):
        with self._lock:
            alerts = [dict(item) for item in self._seen.values()][-20:]
            queued = self.db.execute('SELECT COUNT(*) FROM room_outbox WHERE event_id LIKE ?', (self.scope + '%',)).fetchone()[0]
        with self.session.lock:
            self.session.room = {'status': state, 'detail': detail, 'queued': queued, 'alerts': alerts}
            status = self.session._status()
        self.session._publish({'type': 'status', 'status': status})

    def enqueue(self, person):
        conversations = person.get('conversations') or []
        if not conversations:
            return
        conversation = max(conversations, key=lambda item: item['id'])
        event_id = '%s:%s' % (person['contact_uid'], conversation['id'])
        payload = {'event_id': event_id, 'card': {key: str(person.get(key) or '')[:limit]
                   for key, limit in [('name',150), ('company',200), ('role',200), ('summary',3000), ('working_on',1000)]},
                   'marked': bool(person.get('_marked'))}
        with self._lock:
            if self.db.execute('SELECT COUNT(*) FROM room_outbox WHERE event_id LIKE ?', (self.scope + '%',)).fetchone()[0] >= 100:
                self._status('error', 'Room queue is full; this encounter was not shared.')
                return
            self.db.execute('INSERT OR IGNORE INTO room_outbox VALUES (?, ?)', (self.scope + event_id, json.dumps(payload)))
            self.db.commit()
        self._status('queued', 'Encounter queued for the room.')

    def reset(self):
        with self._lock:
            self._generation += 1
            self.db.execute('DELETE FROM room_outbox WHERE event_id LIKE ?', (self.scope + '%',))
            self.db.commit()
            self._seen.clear()
            self._lit.clear()
        self._status('connecting', 'Local room queue cleared. Already shared room events remain on the room server.')

    def tick(self):
        headers = {'Authorization': 'Bearer ' + self.token}
        with self._lock:
            generation = self._generation
            rows = self.db.execute('SELECT event_id,payload FROM room_outbox WHERE event_id LIKE ? ORDER BY rowid LIMIT 5', (self.scope + '%',)).fetchall()
        try:
            for event_id, payload in rows:
                with self._lock:
                    if generation != self._generation:
                        return
                response = self.client.post(self.url + '/api/encounters', headers=headers, json=json.loads(payload))
                response.raise_for_status()
                with self._lock:
                    if generation != self._generation:
                        return
                    self.db.execute('DELETE FROM room_outbox WHERE event_id=?', (event_id,))
                    self.db.commit()
            response = self.client.get(self.url + '/api/alerts', headers=headers)
            response.raise_for_status()
            for item in response.json().get('alerts', [])[:50]:
                alert_id = str(item['id'])
                with self._lock:
                    if generation != self._generation:
                        return
                    self._seen[alert_id] = item
                    if len(self._seen) > 50:
                        del self._seen[next(iter(self._seen))]
                    lit = alert_id in self._lit
                if not lit and self.session.state()['status']['phase'] == 'idle':
                    lit = bool(self.alert(item))
                    if lit:
                        with self._lock:
                            self._lit.add(alert_id)
                if lit:
                    response = self.client.post(self.url + '/api/alerts/' + alert_id + '/ack', headers=headers)
                    response.raise_for_status()
            self._status('connected', 'Connected. Match alerts glow when the microphone is off.')
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            detail = 'Room connection interrupted; queued encounters will retry.'
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (401, 403):
                detail = 'Room authentication failed. Check the participant token.'
            self._status('error', detail)

    def start(self):
        def run():
            while not self._stop.is_set():
                self.tick()
                self._stop.wait(2)
        self._thread = threading.Thread(target=run, name='remember-room', daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=25)
            if self._thread.is_alive():
                return
        if self._owns_client:
            self.client.close()
        self.db.close()
