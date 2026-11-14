"""FastAPI page: person cards, live updates, recall, and the demo controls."""

import json
import queue
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

STATIC = Path(__file__).resolve().parent / "static"


class TextIn(BaseModel):
    text: str = ""


def create_app(session, play_demo=None) -> FastAPI:
    app = FastAPI(title="Remember")
    app.state.session = session
    app.state.play_demo = play_demo

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
        return session.hear(text, True)

    @app.post("/api/mark")
    def mark():
        return session.mark()

    @app.post("/api/new")
    def new_person():
        person = session.new_person()
        return {"person": person, "status": session.state()["status"]}

    @app.post("/api/end")
    def end():
        person = session.end()
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
