# Remember · VexoHacks

> **Shake hands once. You're in each other's phones — and you'll remember why.**

Remember is a wearable-driven networking app built around Vexo wristbands.

- **Phase 1 – contact capture and swap.** A band listens during a conversation (blue LED). When the conversation ends, AI turns the transcript into a structured contact card: name, company, role, socials, summary, promises, needs and offers. You can export it as an RFC 6350 `.vcf`. When two bands **handshake** or **double-tap within 1.5 s**, the server swaps the two owners' cards. Each side gets its own private note about the conversation (green LED).
- **Conversation history.** Every recorded conversation lands in a chat-style history (like a messaging app, but the "messages" are what the Vexo band recorded). Your lines are on the right and theirs on the left, with handshakes, intros and matches shown as system notes. "Why you met" and the promises are pinned at the top of each chat.
- **Phase 2 – connect the whole room.** Every band reports to one room server. A spoken intro ("*You should meet Sam, he's on the FPGA team*") or a need/offer pair ("*hiring FPGA interns*" ↔ "*looking for an FPGA internship*") sends a live match alert to the two people involved and lights their LEDs. A projector-ready **Room Map** draws the connection.

The demo works **with or without** an OpenAI key. With no key, or if the API call fails or times out, a deterministic heuristic extractor and matcher takes over, so bad Wi-Fi can't stop the demo.

---

## Quick start

```bash
cd remember-web
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional: add OPENAI_API_KEY=sk-...
uvicorn app.main:app --reload --port 8000
```

Then open **http://localhost:8000**. If port 8000 is already in use, run with `--port 8077` and open that port instead.

You don't need a separate frontend build. The UI is plain HTML/CSS/JS in `static/`, and FastAPI serves it. Requires Python 3.9+.

API docs (Swagger) are at **http://localhost:8000/docs**.

## 60-second demo script

1. Click **Load sample room**. This sets up Akim on Band A, Priya on Band B and Sam on Band C, plus eight more attendees on a modeled event floor (stage, company booths, tables, coffee bar) with a few connections already made. Each band has one recorded conversation, and the **Conversations** tab opens on Akim's chat with Priya. The sample headshots are placeholder stock portraits from randomuser.me. Use the camera button on any card to swap in a real LinkedIn photo.
2. Click **Demo** in the top bar, then **Shake hands** with A and B. Both lights flash green, the cards swap, and "You shook hands and swapped cards." appears in both people's chats. A solid green arc appears on the Room map.
3. **"You should meet…"**: pick *"Priya → Akim: You should meet Sam"*. A purple dotted arc appears on the map, with a banner reading *"Priya says Akim & Sam should talk about RISC-V"*.
4. **Find matches**. Orange dashed arcs connect people whose needs and offers fit, for example Akim needs RISC-V mentorship and Sam offers it.
5. **Have a conversation**: pick an example (or write your own) and click *Capture contact*. The band turns solid blue while it works, then green. The new chat opens with the recording as Messages-style bubbles, and the card has **Save to phone** (.vcf).
6. On the Room map, click **Show full screen** (or open `/?projector`) to put the 3D map on the projector.
7. **Clear all data** (bottom of the Demo panel) wipes everything.

## Privacy model

- A band records **only while its light is solid blue**.
- Recordings are saved to **the band owner's own conversation history** and nobody else's. Each recording has a **Delete** button (`DELETE /api/conversations/{id}`); deleting it keeps the contact card.
- Matches and intros are shared **only with the two matched participants**: a card goes only into those two people's phones. The Room map shows that a connection exists, and the banner shows the reason.
- `POST /api/session/reset` (the **Clear all data** button) deletes all people, contacts, recordings and matches.

## API

| Method | Path | What it does |
| --- | --- | --- |
| `POST` | `/api/bands/{band_id}/transcript` | `{ "text": "..." }`. Runs AI extraction, upserts the person and contact card, broadcasts `contact_created`. Any "you should meet X" in the transcript also creates an intro match. |
| `POST` | `/api/bands/{band_id}/gesture` | `{ "type": "tap" \| "double_tap" \| "handshake", "timestamp": <unix s or ms> }`. `tap` toggles listening. Two bands sending `handshake`/`double_tap` within 1.5 s swap cards and broadcast `handshake_swap`. |
| `POST` | `/api/room/match` | `{}` matches needs ↔ offers across the room. `{ "utterance": "...", "speaker_band_id": "B", "listener_band_id": "A" }` parses a spoken intro. Both broadcast `room_match`. |
| `GET` | `/api/contacts?band_id=A` | Lists contact cards (optionally only those in one band owner's phone). |
| `GET` | `/api/contacts/{id}/vcard` | Downloads a vCard 4.0 (`.vcf`) with FN/N/ORG/TITLE/EMAIL/URL/X-SOCIALPROFILE and a NOTE covering what you talked about and what was promised. |
| `GET` | `/api/events` (alias `/events`) | SSE stream: `hello`, `band_status`, `contact_created`, `handshake_swap`, `room_match`, `room_loaded`, `session_reset`. |
| `PUT` | `/api/bands/{band_id}/owner` | Pairs a band with its wearer's own profile (the card that gets swapped). |
| `GET` | `/api/state` | Bands, people, contacts and matches in one call (the UI's resync). |
| `GET` | `/api/bands/{band_id}/threads` | Chat list for one band owner: everyone they talked to or were connected with, newest first, with a last-line preview. |
| `GET` | `/api/bands/{band_id}/threads/{person_id}` | One chat: the contact card plus recordings (as `me`/`them` utterances) and handshake/intro/match events, in time order. |
| `DELETE` | `/api/conversations/{id}` | Deletes one recording (the contact card stays). Broadcasts `conversation_deleted`. |
| `PUT` | `/api/people/{id}/photo` | Sets a profile photo from `{ "url": "..." }` (for example a LinkedIn photo link: right-click their photo, then Copy Image Address) or `{ "data_url": "data:image/..." }` from an upload. The image is downloaded and stored locally, so expiring LinkedIn links keep working. JPEG/PNG/WebP/GIF, up to 3 MB. People with a GitHub link fall back to their GitHub avatar, then to colored initials. |
| `DELETE` | `/api/people/{id}/photo` | Removes a stored photo. |
| `POST` | `/api/demo/seed` | Loads the sample room. |
| `POST` | `/api/session/reset` | Clears all session data. |

### Hooking up real bands

The firmware or phone bridge only needs to call two endpoints. It posts the transcript when a conversation ends (tap again, or a handshake motion burst):

```bash
curl -X POST localhost:8000/api/bands/A/transcript -H 'content-type: application/json' \
  -d '{"text":"Priya: Hi, I am Priya Raman, an engineering manager at Lumen Silicon..."}'
```

It also posts gestures, timestamped on the band:

```bash
curl -X POST localhost:8000/api/bands/A/gesture -H 'content-type: application/json' -d '{"type":"handshake"}'
```

The LED state to show is pushed back on `/api/events` as `band_status` (`idle` | `listening` | `saved` | `matched`).

## Project layout

```
app/
  main.py     FastAPI routes, gesture pairing, LED state, SSE wiring
  ai.py       OpenAI extraction / matching / intro parsing + offline heuristic fallback
  db.py       SQLite schema + queries (people, bands, conversations, contacts, matches)
  vcard.py    RFC 6350 vCard 4.0 builder (escaping + 75-octet line folding)
  events.py   In-process SSE broker
  seed.py     Sample room + preset transcripts / intros
static/
  index.html  styles.css  app.js  favicon.svg
```

## Configuration (`.env`)

| Var | Default | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | *(empty)* | Empty means offline heuristics. Never commit it. |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Any chat model that supports JSON mode. |
| `OPENAI_TIMEOUT` | `20` | Seconds before falling back to heuristics. |
| `REMEMBER_DB` | `./remember.db` | SQLite file path. |
| `REMEMBER_FORCE_FALLBACK` | — | Set to `1` to ignore the key and use the heuristics. |
