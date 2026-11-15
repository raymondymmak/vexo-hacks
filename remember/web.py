"""FastAPI page: person cards, live updates, recall, and the demo controls."""

import json
import os
import httpx
import queue
import secrets
from urllib.parse import urlsplit
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

STATIC = Path(__file__).resolve().parent / "static"


class TextIn(BaseModel):
    text: str = ""
    speaker: str | None = None


def create_app(session, play_demo=None, pairing_token=None, download_secret=None) -> FastAPI:
    app = FastAPI(title="AfterHello")
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    app.state.session = session
    app.state.play_demo = play_demo

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"detail": "Cross-origin requests are not accepted."}, status_code=403)
        if pairing_token and request.url.path.startswith("/api/"):
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
            if request.url.path == "/api/events":
                supplied = supplied or request.query_params.get("token", "")
            if not secrets.compare_digest(supplied.encode(), pairing_token.encode()):
                return JSONResponse({"detail": "Open the paired phone link to connect."}, status_code=401)
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/room/map")
    async def room_map():
        url = os.environ.get('REMEMBER_ROOM_MAP_URL', '').rstrip('/')
        token = os.environ.get('REMEMBER_ROOM_MAP_TOKEN', '')
        if not url or not token:
            return {'connected': False, 'detail': 'No shared room is connected yet.', 'participants': [], 'edges': []}
        try:
            async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
                response = await client.get(url + '/api/state', headers={'Authorization': 'Bearer ' + token})
                response.raise_for_status()
                data = response.json()
            # Only the projector's public fields cross into the dashboard.
            return {'connected': True,
                    'participants': [{k: p[k] for k in ('id', 'name', 'simulated') if k in p} for p in data['participants']],
                    'edges': [{k: e[k] for k in ('id', 'source_id', 'target_id', 'kind', 'created_at') if k in e} for e in data['edges']]}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return JSONResponse({'detail': 'The shared room is unavailable. Reconnecting…'}, status_code=503)

    @app.post("/api/start")
    def start():
        return session.start()

    @app.post("/api/handshake")
    def handshake():
        return session.start()

    @app.post("/api/discard")
    def discard():
        return session.discard()

    @app.post("/api/wearer")
    def wearer(body: dict):
        speaker = body.get("speaker")
        if speaker is not None and not isinstance(speaker, str):
            raise HTTPException(400, "Invalid speaker")
        return session.set_wearer(speaker)

    @app.patch("/api/people/{person_id}")
    def edit(person_id: int, body: dict):
        try:
            person = session.store.update_person(person_id, body)
        except (ValueError, TypeError) as error:
            raise HTTPException(400, str(error))
        if person is None:
            raise HTTPException(404, "Contact not found")
        session._publish({"type": "card", "person": person, "status": session.state()["status"]})
        if session.on_contact_ready:
            session.on_contact_ready(person)
        return person

    @app.get("/api/people/{person_id}/contact.vcf")
    def contact(person_id: int):
        from .vcard import render_contact
        person = session.store.get(person_id)
        if person is None:
            raise HTTPException(404, "Contact not found")
        try:
            content = render_contact(person)
        except ValueError as error:
            raise HTTPException(409, str(error))
        return Response(content, media_type="text/vcard", headers={
            "Content-Disposition": 'attachment; filename="contact-%d.vcf"' % person_id})

    @app.get("/contact/{person_id}.vcf")
    def notification_contact(person_id: int, key: str = ""):
        from .contact_flow import valid_contact_key
        from .vcard import render_contact
        person = session.store.get(person_id)
        if not download_secret or not person or not valid_contact_key(download_secret, person, key):
            raise HTTPException(404, "Contact link unavailable")
        return Response(render_contact(person), media_type="text/vcard", headers={
            "Content-Disposition": 'attachment; filename="contact-%d.vcf"' % person_id})

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/state")
    def state(q: str = ""):
        return session.state(q)

    @app.post("/api/say")
    def say(body: TextIn):
        text = " ".join((body.text or "").split())
        if not text:
            raise HTTPException(status_code=400, detail="Type a line first.")
        if len(text) > 4000:
            raise HTTPException(status_code=400, detail="That line is too long.")
        return session.hear(text, True, speaker=body.speaker)

    @app.post("/api/mark")
    def mark():
        return session.mark()

    @app.post("/api/new")
    def new_person():
        person = session.new_person()
        return {"person": person, "status": session.state()["status"]}

    @app.post("/api/end")
    def end(body: dict | None = None):
        person_id = (body or {}).get("person_id")
        if person_id is not None and (type(person_id) is not int or session.store.get(person_id) is None):
            raise HTTPException(400, "Choose an existing contact")
        person = session.end(person_id=person_id)
        return {"person": person, "status": session.state()["status"]}

    @app.post("/api/reset")
    def reset():
        return session.reset()

    @app.post("/api/demo")
    def demo():
        if play_demo is None:
            raise HTTPException(status_code=400, detail="The demo is not available.")
        started = play_demo()
        if not started:
            raise HTTPException(status_code=409, detail="The demo is already playing.")
        return {"started": True}

    @app.get("/api/events")
    def events():
        inbox = session.subscribe()

        def gen():
            try:
                while True:
                    try:
                        item = inbox.get(timeout=15)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    if item is None:
                        break
                    yield "data: %s\n\n" % json.dumps(item)
            finally:
                session.unsubscribe(inbox)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    return app
