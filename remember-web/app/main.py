"""Remember — FastAPI room server.

Run:  uvicorn app.main:app --reload --port 8000
"""
import asyncio
import base64
import binascii
import json
import re
import logging
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, Response, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import AliasChoices, BaseModel, Field  # noqa: E402

from . import ai, db, seed  # noqa: E402
from .events import broker  # noqa: E402
from .vcard import build_vcard, vcard_filename  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("remember")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PAIR_WINDOW_S = 1.5
SAVED_LED_S = 4.0
MATCH_LED_S = 6.0

@asynccontextmanager
async def lifespan(_app: "FastAPI"):
    db.conn()
    log.info("AI mode: %s (model %s)", "openai" if ai.ai_enabled() else "heuristic fallback", ai.MODEL)
    # Uvicorn waits for open connections before exiting, and SSE streams never finish on their own,
    # so Ctrl+C / --reload would hang. Chain onto uvicorn's signal handlers to close the streams first.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        prev = signal.getsignal(sig)
        if callable(prev):
            def _close_then_exit(signum, frame, prev=prev):
                loop.call_soon_threadsafe(broker.close_all)
                prev(signum, frame)
            signal.signal(sig, _close_then_exit)
    yield
    broker.close_all()


app = FastAPI(title="Remember", version="1.0.0", lifespan=lifespan,
              description="Wearable-driven networking room server (VexoHacks).")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# band_id -> (gesture type, timestamp seconds) waiting for a partner
_pending_gestures: Dict[str, tuple] = {}
_led_tasks: Dict[str, asyncio.Task] = {}


# ================================================================ helpers
def _owner_name(band: Optional[Dict[str, Any]]) -> str:
    if not band:
        return ""
    return (band.get("owner") or {}).get("name") or ""


def _first(name: str) -> str:
    return (name or "").split()[0] if name else "Someone"


def set_led(band_id: str, led: str, revert_after: Optional[float] = None, **extra: Any) -> None:
    """Change a band's LED, broadcast it, and optionally fall back to idle later."""
    db.set_band_led(band_id, led)
    t = _led_tasks.pop(band_id, None)
    if t and not t.done():
        t.cancel()
    broker.publish("band_status", {"band": db.get_band(band_id), **extra})
    if revert_after:
        async def _revert() -> None:
            try:
                await asyncio.sleep(revert_after)
                db.set_band_led(band_id, "idle")
                broker.publish("band_status", {"band": db.get_band(band_id)})
            except asyncio.CancelledError:
                pass
        _led_tasks[band_id] = asyncio.create_task(_revert())


def _require_band(band_id: str) -> Dict[str, Any]:
    band_id = band_id.strip().upper()
    if not band_id or len(band_id) > 8 or not band_id.isalnum():
        raise HTTPException(400, "band_id must be short and alphanumeric (e.g. A, B, C)")
    return db.ensure_band(band_id)


def _state() -> Dict[str, Any]:
    return {
        "bands": db.list_bands(),
        "people": db.list_people(),
        "contacts": db.list_contacts(),
        "matches": db.list_matches(),
        "conversation_count": db.count_conversations(),
        "room": db.get_setting("room_layout"),
        "ai": {"enabled": ai.ai_enabled(), "model": ai.MODEL},
    }


def _share_match_with_pair(a_id: int, b_id: int, source: str, reason: str) -> List[int]:
    """Matches are private: only the two matched people get each other's card."""
    contact_ids = []
    for me, other in ((a_id, b_id), (b_id, a_id)):
        band = db.band_for_person(me)
        if band:
            existing = db.get_contact_row(band, other)
            if existing and existing["match_reason"] and reason not in existing["match_reason"]:
                fields = {"match_reason": f"{existing['match_reason']} · {reason}"}
            else:
                fields = {"match_reason": reason}
            if not (existing and existing["why_remember"]):
                fields["why_remember"] = reason
            contact_ids.append(db.upsert_contact(band, other, source=source, **fields))
            set_led(band, "matched", revert_after=MATCH_LED_S)
    return contact_ids


def _publish_match(match_id: int, kind: str, headline: str, engine: str) -> Dict[str, Any]:
    m = db.get_match(match_id)
    payload = {"match": m, "kind": kind, "headline": headline, "engine": engine,
               "a": db.get_person(m["a"]), "b": db.get_person(m["b"])}
    broker.publish("room_match", payload)
    return payload


def create_intro(a_id: int, b_id: int, introducer_id: Optional[int], topic: str, engine: str) -> Dict[str, Any]:
    a, b = db.get_person(a_id), db.get_person(b_id)
    intro = db.get_person(introducer_id) if introducer_id else None
    topic = (topic or "").strip()
    if intro and introducer_id not in (a_id, b_id):
        headline = f"{_first(intro['name'])} says {_first(a['name'])} & {_first(b['name'])} should talk {topic}".strip()
    else:
        headline = f"{_first(a['name'])} & {_first(b['name'])} should talk {topic}".strip()
    mid = db.add_match(a_id, b_id, "intro", headline, introducer_id)
    _share_match_with_pair(a_id, b_id, "intro", headline)
    return _publish_match(mid, "intro", headline, engine)


# ================================================================ pages
@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "ai": {"enabled": ai.ai_enabled(), "model": ai.MODEL}}


@app.get("/api/state")
async def state() -> Dict[str, Any]:
    return _state()


@app.get("/api/presets")
async def presets() -> Dict[str, Any]:
    return {"transcripts": seed.PRESET_TRANSCRIPTS, "intros": seed.PRESET_INTROS}


# ================================================================ SSE
async def _sse(request: Request) -> StreamingResponse:
    return StreamingResponse(
        broker.stream(request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """SSE: hello, band_status, contact_created, handshake_swap, room_match, room_loaded, session_reset."""
    return await _sse(request)


@app.get("/events", include_in_schema=False)
async def events_alias(request: Request) -> StreamingResponse:
    return await _sse(request)


# ================================================================ bands
class OwnerIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    company: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""
    socials: Dict[str, str] = {}
    needs: List[str] = []
    offers: List[str] = []


@app.get("/api/bands")
async def bands() -> List[Dict[str, Any]]:
    return db.list_bands()


@app.put("/api/bands/{band_id}/owner")
async def set_owner(band_id: str, body: OwnerIn) -> Dict[str, Any]:
    """Pair a band with its wearer's own profile (the card that gets swapped on handshake)."""
    band = _require_band(band_id)
    data = body.model_dump()
    owner = band.get("owner")
    if owner:
        with db._lock:
            db.conn().execute(
                "UPDATE people SET name=?, company=?, role=?, email=?, phone=?, socials=?, needs=?, offers=? WHERE id=?",
                (data["name"], data["company"], data["role"], data["email"], data["phone"],
                 json.dumps(data["socials"]), json.dumps(data["needs"]), json.dumps(data["offers"]), owner["id"]),
            )
            db.conn().commit()
    else:
        existing = db.find_person_by_name(data["name"])
        pid = existing["id"] if existing and not db.band_for_person(existing["id"]) else db.create_person(data)
        if existing and pid == existing["id"]:
            db.merge_person(pid, data)
        db.set_band_owner(band["id"], pid)
    broker.publish("band_status", {"band": db.get_band(band["id"]), "profile_updated": True})
    return db.get_band(band["id"])


class TranscriptIn(BaseModel):
    text: str = Field(..., min_length=10, max_length=20000, validation_alias=AliasChoices("text", "transcript"))


@app.post("/api/bands/{band_id}/transcript")
async def ingest_transcript(band_id: str, body: TranscriptIn) -> Dict[str, Any]:
    band = _require_band(band_id)
    bid = band["id"]
    owner = band.get("owner")
    set_led(bid, "listening")
    started = time.time()
    try:
        people = db.list_people()
        extraction, engine = await ai.extract_contact(body.text, _owner_name(band), [p["name"] for p in people])
    except Exception:
        set_led(bid, "idle")
        raise
    if engine == "heuristic":  # keep the blue LED visible long enough to see on stage
        await asyncio.sleep(max(0.0, 1.2 - (time.time() - started)))

    cp = extraction["counterpart"]
    person = db.find_person_by_name(cp["name"]) if cp["name"] != "Unknown" else None
    if person and owner and person["id"] == owner["id"]:
        person = None  # the model extracted the wearer themself; don't merge into their own card
    if person:
        pid = person["id"]
        db.merge_person(pid, cp)
    else:
        pid = db.create_person(cp)
    if owner and (extraction["wearer"]["needs"] or extraction["wearer"]["offers"]):
        db.merge_person(owner["id"], extraction["wearer"])

    utterances = ai.split_utterances(body.text, _owner_name(band), cp["name"])
    db.add_conversation(bid, pid, extraction["conversation_summary"], extraction, engine, transcript=utterances)
    cid = db.upsert_contact(
        bid, pid,
        source="conversation",
        conversation_summary=extraction["conversation_summary"],
        why_remember=extraction["why_remember"],
        promises=extraction["promises_made"],
    )
    contact = db.get_contact(cid)
    set_led(bid, "saved", revert_after=SAVED_LED_S)
    broker.publish("contact_created", {"contact": contact, "band_id": bid, "engine": engine})

    # Spoken intros inside the conversation ("you should meet Sam") -> Phase 2 room match.
    intro_events = []
    if owner:
        for intro in extraction["intros"]:
            target = db.find_person_by_name(intro["name"])
            if target and target["id"] not in (owner["id"], pid):
                topic = ai.topic_from_tail(intro["reason"]) if engine == "heuristic" else intro["reason"]
                if topic and not topic.lower().startswith(("about", "—", "-")):
                    topic = f"about {topic}"
                intro_events.append(create_intro(owner["id"], target["id"], pid, topic, engine))

    return {"contact": contact, "extraction": extraction, "engine": engine, "intros": intro_events}


class GestureIn(BaseModel):
    type: str = Field(..., pattern="^(tap|double_tap|handshake)$")
    timestamp: Optional[float] = Field(None, description="Unix time in seconds or milliseconds; defaults to now")


@app.post("/api/bands/{band_id}/gesture")
async def gesture(band_id: str, body: GestureIn) -> Dict[str, Any]:
    band = _require_band(band_id)
    bid = band["id"]
    ts = body.timestamp or time.time()
    if ts > 1e11:  # milliseconds
        ts /= 1000.0

    if body.type == "tap":
        # Single tap toggles listening: start of conversation -> blue LED, tap again -> conversation ends.
        new = "idle" if band["led"] == "listening" else "listening"
        set_led(bid, new, gesture="tap")
        return {"status": "listening" if new == "listening" else "stopped", "band": db.get_band(bid)}

    # Drop stale pending gestures.
    for k, (_, t) in list(_pending_gestures.items()):
        if abs(ts - t) > PAIR_WINDOW_S * 4:
            _pending_gestures.pop(k, None)

    partner, best = None, None
    for other, (_, t) in _pending_gestures.items():
        if other != bid and abs(ts - t) <= PAIR_WINDOW_S and (best is None or abs(ts - t) < best):
            partner, best = other, abs(ts - t)

    if not partner:
        _pending_gestures[bid] = (body.type, ts)
        broker.publish("band_status", {"band": db.get_band(bid), "gesture": body.type, "pairing": True})
        return {"status": "waiting", "window_s": PAIR_WINDOW_S, "band": db.get_band(bid)}

    _pending_gestures.pop(partner, None)
    _pending_gestures.pop(bid, None)
    return await handshake_swap(partner, bid, body.type, best or 0.0)


async def handshake_swap(a_id: str, b_id: str, gesture_type: str, delta: float) -> Dict[str, Any]:
    a, b = db.get_band(a_id), db.get_band(b_id)
    oa, ob = a.get("owner"), b.get("owner")
    if not oa or not ob:
        missing = a_id if not oa else b_id
        raise HTTPException(409, f"Band {missing} has no owner profile yet — load the sample room or set an owner first.")

    contacts = []
    for me_band, other in ((a_id, ob), (b_id, oa)):
        conv = db.latest_conversation(me_band, within_s=1800)
        fields: Dict[str, Any] = {"source": "handshake"}
        if conv:
            cp_id = conv["counterpart_person_id"]
            cp = db.get_person(cp_id) if cp_id else None
            # A card captured from audio where no name was heard is assumed to be the person you just shook hands with.
            if cp and cp_id != other["id"] and cp["name"] == "Unknown" and not db.band_for_person(cp_id):
                db.merge_person(other["id"], cp)
                db.move_contacts(cp_id, other["id"])
                db.move_matches(cp_id, other["id"])
                db.relink_conversation(conv["id"], other["id"])
                db.delete_person_if_orphan(cp_id)
                cp_id = other["id"]
            if cp_id == other["id"]:
                ext = conv.get("extraction") or {}
                fields.update(conversation_summary=conv["summary"],
                              why_remember=ext.get("why_remember", ""), promises=ext.get("promises_made", []))
        if not db.get_contact_row(me_band, other["id"]) and not fields.get("conversation_summary"):
            fields["conversation_summary"] = f"Swapped cards with {_first(other['name'])} via {gesture_type.replace('_', '-')}."
        cid = db.upsert_contact(me_band, other["id"], **fields)
        contacts.append(db.get_contact(cid))

    mid = db.add_match(oa["id"], ob["id"], "handshake", f"{_first(oa['name'])} & {_first(ob['name'])} swapped cards")
    set_led(a_id, "saved", revert_after=SAVED_LED_S)
    set_led(b_id, "saved", revert_after=SAVED_LED_S)
    payload = {
        "bands": [a_id, b_id],
        "gesture": gesture_type,
        "delta_s": round(delta, 3),
        "contacts": contacts,
        "match": db.get_match(mid),
        "headline": f"{_first(oa['name'])} and {_first(ob['name'])} swapped cards.",
    }
    broker.publish("handshake_swap", payload)
    return {"status": "swapped", **payload}


# ================================================================ room
class MatchIn(BaseModel):
    utterance: Optional[str] = Field(None, max_length=2000, description="Spoken intro, e.g. 'You should meet Sam'")
    speaker_band_id: Optional[str] = None
    listener_band_id: Optional[str] = None


@app.post("/api/room/match")
async def room_match(body: Optional[MatchIn] = None) -> Dict[str, Any]:
    """With `utterance`: parse a spoken intro. Without: match needs <-> offers across the room."""
    body = body or MatchIn()
    people = db.list_people()
    if body.utterance and body.utterance.strip():
        speaker = (db.get_band(body.speaker_band_id.upper()) or {}).get("owner") if body.speaker_band_id else None
        listener = (db.get_band(body.listener_band_id.upper()) or {}).get("owner") if body.listener_band_id else None
        for bid in (body.speaker_band_id, body.listener_band_id):
            if bid:
                set_led(bid.upper(), "listening")
        result, engine = await ai.parse_intro(body.utterance, people, speaker, listener)
        if engine == "heuristic":
            await asyncio.sleep(0.8)
        if not result:
            for bid in (body.speaker_band_id, body.listener_band_id):
                if bid:
                    set_led(bid.upper(), "idle")
            return {"matches": [], "engine": engine, "message": "No introduction detected in that utterance."}
        for bid in (body.speaker_band_id, body.listener_band_id):
            if bid:
                set_led(bid.upper(), "idle")
        topic = result.get("topic") or ""
        if topic and not topic.lower().startswith(("about", "—", "-", "on ")):
            topic = f"about {topic}"
        ev = create_intro(result["a"], result["b"], speaker["id"] if speaker else None, topic, engine)
        return {"matches": [ev], "engine": engine}

    # Already-swapped pairs don't need a match; intro'd pairs can still get a need<->offer line.
    skip = {(min(m["a"], m["b"]), max(m["a"], m["b"])) for m in db.list_matches() if m["kind"] in ("handshake", "need_offer")}
    found, engine = await ai.match_room(people, skip)
    events_out = []
    for m in found:
        mid = db.add_match(m["a"], m["b"], "need_offer", m["reason"])
        _share_match_with_pair(m["a"], m["b"], "match", m["reason"])
        events_out.append(_publish_match(mid, "need_offer", m["reason"], engine))
    return {"matches": events_out, "engine": engine,
            "message": None if events_out else "No new need ↔ offer matches right now."}


# ================================================================ contacts
@app.get("/api/contacts")
async def contacts(band_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return db.list_contacts(band_id.upper() if band_id else None)


@app.get("/api/contacts/{contact_id}")
async def contact(contact_id: int) -> Dict[str, Any]:
    c = db.get_contact(contact_id)
    if not c:
        raise HTTPException(404, "Contact not found")
    return c


@app.get("/api/contacts/{contact_id}/vcard")
async def contact_vcard(contact_id: int) -> Response:
    c = db.get_contact(contact_id)
    if not c:
        raise HTTPException(404, "Contact not found")
    # Put their picture in the card so it shows up in the phone's Contacts app.
    photo = None
    found = db.get_photo(c["person_id"])
    if found:
        photo = found
    elif c.get("photo", "").startswith("https://"):
        try:
            async with httpx.AsyncClient(timeout=4, follow_redirects=True) as client:
                r = await client.get(c["photo"])
                if r.status_code == 200 and len(r.content) <= MAX_PHOTO_BYTES and _sniff_image(r.content):
                    photo = (r.content, _sniff_image(r.content))
        except httpx.HTTPError:
            pass  # offline: the card still works, just without a picture
    return Response(
        content=build_vcard(c, photo),
        media_type="text/vcard; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{vcard_filename(c)}"'},
    )


# ================================================================ profile photos
MAX_PHOTO_BYTES = 3 * 1024 * 1024


def _sniff_image(b: bytes) -> Optional[str]:
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


async def _download_image(url: str) -> bytes:
    if not re.match(r"^https://[^/\s]+/", url) or re.match(r"^https://(localhost|127\.|0\.|\[)", url):
        raise HTTPException(400, "Use a public https:// image link.")
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (Remember room server)"}) as client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                data = b""
                async for chunk in r.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_PHOTO_BYTES:
                        raise HTTPException(413, "That image is over 3 MB.")
                return data
    except httpx.HTTPError:
        raise HTTPException(400, "Couldn't download that image. LinkedIn links expire after a while: "
                                 "save the photo and upload it instead.")


class PhotoIn(BaseModel):
    url: Optional[str] = Field(None, max_length=4000, description="Image link, e.g. a LinkedIn profile photo")
    data_url: Optional[str] = Field(None, max_length=5_000_000, description="data:image/...;base64,... from an upload")


@app.put("/api/people/{person_id}/photo")
async def set_person_photo(person_id: int, body: PhotoIn) -> Dict[str, Any]:
    """Set someone's profile photo from a pasted link (LinkedIn etc.) or an uploaded file. Stored locally."""
    if not db.get_person(person_id):
        raise HTTPException(404, "Person not found")
    if body.data_url:
        m = re.match(r"^data:image/[\w.+-]+;base64,(.+)$", body.data_url, re.S)
        if not m:
            raise HTTPException(400, "Upload a JPEG, PNG, WebP or GIF image.")
        try:
            data = base64.b64decode(m.group(1), validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "That upload couldn't be read.")
    elif body.url and body.url.strip():
        data = await _download_image(body.url.strip())
    else:
        raise HTTPException(400, "Paste an image link or choose a file.")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "That image is over 3 MB.")
    ctype = _sniff_image(data)
    if not ctype:
        raise HTTPException(400, "That isn't a JPEG, PNG, WebP or GIF image.")
    db.set_photo(person_id, data, ctype)
    person = db.get_person(person_id)
    broker.publish("person_updated", {"person": person})
    return person


@app.delete("/api/people/{person_id}/photo")
async def delete_person_photo(person_id: int) -> Dict[str, Any]:
    if not db.get_person(person_id):
        raise HTTPException(404, "Person not found")
    db.clear_photo(person_id)
    person = db.get_person(person_id)
    broker.publish("person_updated", {"person": person})
    return person


@app.get("/api/people/{person_id}/photo")
async def get_person_photo(person_id: int) -> Response:
    found = db.get_photo(person_id)
    if not found:
        raise HTTPException(404, "No photo")
    data, ctype = found
    # URLs carry ?v=<version>, so the browser can cache aggressively.
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ================================================================ conversation history (chat view)
def _event_text(m: Dict[str, Any]) -> str:
    if m["kind"] == "handshake":
        return "You shook hands and swapped cards."
    if m["kind"] == "intro":
        return m["reason"]
    return f"Match: {m['reason']}"


def _thread_items(band_id: str, person_id: int) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = [{"type": "recording", **c} for c in db.conversations_with(band_id, person_id)]
    owner = (db.get_band(band_id) or {}).get("owner")
    if owner:
        for m in db.list_matches():
            if {m["a"], m["b"]} == {owner["id"], person_id}:
                items.append({"type": "event", "kind": m["kind"], "text": _event_text(m), "created_at": m["created_at"]})
    return sorted(items, key=lambda x: x["created_at"])


@app.get("/api/bands/{band_id}/threads")
async def threads(band_id: str) -> List[Dict[str, Any]]:
    """Chat list for one band owner: everyone they've talked to or been connected with, newest first."""
    band = _require_band(band_id)
    out = []
    for c in db.list_contacts(band["id"]):
        items = _thread_items(band["id"], c["person_id"])
        last = items[-1] if items else None
        if last and last["type"] == "recording" and last["utterances"]:
            u = last["utterances"][-1]
            preview = ("You: " if u["who"] == "me" else "") + u["text"]
        elif last:
            preview = last.get("text") or last.get("summary") or ""
        else:
            preview = c["why_remember"] or c["match_reason"] or ""
        out.append({
            "person_id": c["person_id"], "contact_id": c["id"], "name": c["name"], "role": c["role"], "photo": c["photo"],
            "company": c["company"], "source": c["source"], "preview": preview,
            "recordings": sum(1 for i in items if i["type"] == "recording"),
            "last_at": items[-1]["created_at"] if items else c["updated_at"],
        })
    return sorted(out, key=lambda t: t["last_at"], reverse=True)


@app.get("/api/bands/{band_id}/threads/{person_id}")
async def thread(band_id: str, person_id: int) -> Dict[str, Any]:
    band = _require_band(band_id)
    row = db.get_contact_row(band["id"], person_id)
    if not row:
        raise HTTPException(404, "No conversation with that person on this band")
    return {"band_id": band["id"], "owner": band.get("owner"), "contact": db.get_contact(row["id"]),
            "items": _thread_items(band["id"], person_id)}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int) -> Dict[str, Any]:
    """Privacy control: delete one recording. The contact card stays."""
    if not db.delete_conversation(conversation_id):
        raise HTTPException(404, "Recording not found")
    broker.publish("conversation_deleted", {"id": conversation_id})
    return {"deleted": conversation_id}


# ================================================================ demo / session
@app.post("/api/demo/seed")
async def demo_seed() -> Dict[str, Any]:
    _pending_gestures.clear()
    seed.load_sample_room()
    st = _state()
    broker.publish("room_loaded", {"people": len(st["people"])})
    return st


@app.post("/api/session/reset")
async def session_reset() -> Dict[str, Any]:
    _pending_gestures.clear()
    for t in _led_tasks.values():
        t.cancel()
    _led_tasks.clear()
    db.reset()
    broker.publish("session_reset", {})
    return _state()
