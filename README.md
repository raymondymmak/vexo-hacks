# AfterHello

Meet someone. Remember them on your phone.

One Vexo Open band captures a conversation through Pipecat and Deepgram. End the conversation to create a contact with their name, company, role, contact details, notes, and attributed promises. With automatic delivery configured, AfterHello saves the contact to iCloud through CardDAV and sends an ntfy notification. A downloadable vCard is always available as a fallback.

This version is one-to-one capture with one wearer. It does not exchange contacts between bands or match people across a room.

## Set up

Use Python 3.11 or newer; development uses Python 3.12.

```sh
sh scripts/setup.sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The setup script clones the Vexo Open kit into the ignored `vexo-open-kit/` directory. The application uses its band and audio libraries without modifying the kit.

## Offline demo

```sh
.venv/bin/python -m remember --demo --port 8001
```

Open the paired localhost link printed in the terminal (port 8001). The scripted conversations produce editable contacts. Try Start, typed lines, Mark, End, search, and Download contact. The demo uses the built-in extractor and never sends contacts, notifications, or social searches, even when delivery credentials are configured.

To print the demo cards and exit:

```sh
.venv/bin/python -m remember --demo --once
```

The offline clip path exercises PCM through Pipecat, but its mock transcriber emits scripted text rather than transcribing the recording:

```sh
.venv/bin/python -m remember --clip vexo-open-kit/dashboard/speech/sample-speech.opus --once
```

## Your band and phone

Copy `env.example` to `.env` and fill in your team's `DEEPGRAM_API_KEY` and `OPENAI_API_KEY` locally. Keep keys and passwords out of Git and chat.

```sh
cp env.example .env
.venv/bin/python -m pip install bleak
.venv/bin/python -m remember NUMBER --lan --port 8001
```

Replace `NUMBER` with the number on your band's bag. Close the kit dashboard first: the band accepts one Bluetooth connection. Double-tap the band to make it discoverable before starting the command. The program targets only your specified band.

`--lan` prints a paired phone link. Open that exact link on an iPhone on the same Wi-Fi; it contains the token needed to view your contacts and control the session. Without `--lan`, the page is available only on the laptop.

Tell the other person before recording. Once connected, double-tap or press Start to begin capture, and double-tap or press End to finish. A single tap marks a moment. The microphone's blue light indicates listening; audio is sent to Deepgram. Choose which transcript speaker is you before ending if you want promises attributed to you. Uncertain details remain available for review rather than becoming confirmed wearer promises.

Use the page's existing-contact selector and End button to explicitly associate a new conversation with a saved person. Band-ended conversations create separate encounters, even when names match.

`--handshake` enables experimental motion-based starts while idle. It is off by default and still requires real-band validation. Repeated movement during a conversation must not start another one; double-tap remains the fallback.

## Automatic contact delivery

Set `REMEMBER_AUTO_DELIVER=1` in `.env` to opt in. Delivery runs after the contact is saved locally, without waiting for a review click. Review and edit the resulting card whenever needed; edits queue a fresh delivery, and the UI reports each delivery channel separately. Reset cancels queued work; a provider request already sent cannot be recalled.

For iCloud:

1. Enable Contacts sync for your iCloud account on the iPhone.
2. Generate an app-specific password in your Apple Account and enter it as `ICLOUD_APP_PASSWORD`; enter your account username as `ICLOUD_USERNAME`. Apple requires two-factor authentication for app-specific passwords: [Apple's setup instructions](https://support.apple.com/en-us/102654).
3. Leave `ICLOUD_ADDRESSBOOK_URL` empty for discovery. If multiple address books require a selection, set it to the intended HTTPS iCloud address-book collection URL.

For notifications, install/open ntfy on your phone and subscribe to your chosen topic yourself. Set `NTFY_URL` to its full HTTPS topic URL, and set `NTFY_TOKEN` when your server/topic requires authentication. AfterHello publishes to that topic; it does not subscribe the phone automatically.

Optionally set `REMEMBER_PUBLIC_URL` to the base URL where your running AfterHello instance is reachable from the phone. Notifications can then include a contact-scoped vCard fallback link. This setting does not expose or host the server for you. Use your own reachable address; a laptop's `localhost` address will not work on a phone. Fallback links require HTTPS, expire after 15 minutes, and stop working after a server restart. They are attached only if the iCloud save fails.

Social lookup is best-effort for live captures. `REMEMBER_SOCIAL_LOOKUP=1` enables it (the default); `0` disables it. It uses the supplied name and extracted contextual facts to seek a supported public LinkedIn profile, and leaves the URL empty when uncertain or unavailable. Offline demos never search.

With `GEMINI_API_KEY` set, Gemini uses Google Search grounding to find a personal LinkedIn profile after the conversation ends. It receives extracted name, company, role, project, and summary context (not the full transcript), and is prompted to refine searches with those clues. Without a company, at least two contextual fields must match. The URL must be present in search sources and the model must report high confidence; ambiguous results stay blank. Source matching does not independently verify every identity claim, so review the result. `REMEMBER_GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`. When Gemini has no validated result (or no Gemini key is configured), lookup falls back to OpenAI web search with `REMEMBER_SOCIAL_MODEL` defaulting to `gpt-4.1-mini`. Cited prose is normalized in a separate JSON step while retaining the original sources for URL validation. First names can match discovered full names when context supports them; regional LinkedIn URLs are normalized to www.linkedin.com. Lookup errors are displayed separately from no-match results.

The Live processing blocks show capture, final transcription, extraction (including built-in fallback), local save, LinkedIn lookup, iCloud sync, and phone notification. Each delivery result remains attached to its contact, so a later conversation does not inherit an earlier job's status.

The demo target is fast contact-to-phone delivery, not a guaranteed ten-second result. Displayed elapsed time starts when capture is ended and includes extraction and background enrichment/delivery; it excludes later iPhone Contacts sync. A successful server write or notification publish is not proof the phone has displayed it. Failed delivery leaves the local card and manual vCard download available.

## Data and tests

The Full transcript tab shows live speaker-labelled dialogue and saved conversation history, with a text download. Full transcripts are retained locally in SQLite after saving, including speaker labels and wearer attribution. They are not included in iCloud vCards, social lookup queries, or ntfy notifications. Discard capture removes only the current unsaved dialogue; Reset deletes local saved contacts and conversations. Previously discarded transcripts cannot be recovered. Existing databases are migrated additively.

Audio flow:

`band or clip PCM → Pipecat transport → Deepgram speech-to-text → session → contact extraction → local save → optional enrichment/delivery`

The UI is FastAPI plus server-sent events. Local saving and vCard export work when delivery is disabled or unavailable.

```sh
REMEMBER_STT=mock REMEMBER_EXTRACTOR=builtin .venv/bin/python -m unittest discover -s tests -v
```

Tests use simulated input and mocked provider responses. Real-band audio, handshake detection, iCloud credentials, notification subscriptions, and final iPhone sync require separate hardware/account validation.

## Phase 2: connect the room

Run three simulated Vexo bands against a local room server (no speech/model/cloud calls):

```sh
.venv/bin/python -m remember.room_demo --port 8003
```

Open the printed projector link. Alex meets Priya, Priya recommends Sam, and the FPGA internship need matches Sam's hiring offer. The map shows meeting, introduction, and match edges; Alex and Sam's simulated bands flash after capture ends. This tests three separate bridges and kit simulators, not three physical bands.

For a shared room, start `.venv/bin/python -m remember.room --port 8003 --lan`. The admin link can run a graph-only demo, or register participants with authenticated `POST /api/participants` (fields: `id`, `name`, optional `company`, `role`, `needs`, `offers`, `simulated`). Save each returned participant token on that participant's laptop as `REMEMBER_ROOM_TOKEN`, and set `REMEMBER_ROOM_URL` to the room server's LAN address. Start each band's normal AfterHello command. Each real band needs its own laptop. The projector token cannot register people, submit encounters, or read private alerts.

Each bridge shares extracted encounter notes, not full transcripts or contact coordinates. Room matching resolves explicit introductions only to unambiguous registered names, and compares specific needs with offers. The deterministic matcher runs locally by default; `REMEMBER_ROOM_MATCHER=openai` optionally filters evidence-backed candidates through OpenAI. Incoming connections appear privately on the wearer dashboard. Band LEDs wait until capture ends. The room map exposes only participant names and connection types, never private match reasons.

Room records persist in SQLite. Outgoing encounter summaries queue locally during outages; event IDs prevent duplicate processing. Resetting a local capture cannot recall data already shared to the room. The room admin can reset the room, which also invalidates participant tokens. Real multi-band alerts and iPhone delivery still require hardware validation; a successful notification does not establish that iCloud contact sync succeeded.

The main dashboard adapts the visual design from `claude/remember-web-app` (a1bfd27) onto the existing AfterHello backend: Conversations, Contacts, Full transcript, and private Room connections. It retains paired access, live processing stages, saved dialogue, editing, and vCard/text downloads. The separate `remember-web` backend is not required.

### LinkedIn regression checks

`tests/test_linkedin_profiles.py` covers Raymond Mak and Timothy Do using first-name-plus-context inputs, exact expected URLs, and rejection of missing citations, low confidence, conflicting names, or insufficient context. These offline tests mock provider output; expected surnames and profile URLs are never injected into the search input.

Run the separate live discovery check explicitly:

```sh
.venv/bin/python scripts/check_linkedin_live.py
# Or one case:
.venv/bin/python scripts/check_linkedin_live.py --case timothy_do
```

This calls the configured providers and reports the actual URL, elapsed time, and whether it matches the expected profile. It does not modify contacts or send notifications. Fixture context comes from the confirmed LinkedIn profile and Timothy's public resume, with source URLs recorded in the fixture. Live search can change over time; the checker reports failure rather than substituting the expected URL.

### Shared-field recommendations

The room recommends registered people who share a specific professional field after an encounter identifies a unique participant. It uses saved role/project/summary facts (for example, firmware and physical AI both map to hardware and embedded systems), keeps recommendations separate from meetings, and delivers the reason privately to both people. Generic “tech” interests and ambiguous names do not trigger these matches.

Import selected local contacts into the room roster with `scripts/register_room_contacts.py --contact CONTACT_ID:PARTICIPANT_ID`. Supply `REMEMBER_ROOM_URL` and `REMEMBER_ROOM_ADMIN_TOKEN`; add `--wearer PARTICIPANT_ID` when updating this laptop's wearer, then restart the band backend. Registration refreshes that participant's access token. Only selected professional profile fields are shared; transcripts and contact coordinates stay local. Set `REMEMBER_ROOM_ADMIN_TOKEN` and `REMEMBER_ROOM_PROJECTOR_TOKEN` for stable room-server access across restarts. Recommendations run after **End & save**, when extraction completes.

### ChatGPT Dot events through Cloudflare

Run a dedicated MCP service (the band backend need not restart):

```sh
.venv/bin/python -m remember.dot_events --port 8010
cloudflared tunnel --url http://127.0.0.1:8010 --no-autoupdate
```

Set a random `AFTERHELLO_MCP_TOKEN` of at least 32 characters in `.env` first. Connect the tunnel's `/mcp` endpoint using bearer authentication, or use `/mcp/TOKEN` as a private capability URL when the plugin setup cannot attach a bearer header. Treat that complete URL like a password. The tunnel exposes only this separate service, not the dashboard. Quick Tunnel addresses change when the tunnel restarts; use a named Cloudflare tunnel for a stable installation.

The server supports MCP Streamable HTTP JSON responses, `initialize`, `tools/list`, `tools/call`, and `events/list`, `events/subscribe`, `events/unsubscribe`. It advertises `conversation.completed` and a read-only `afterhello_status` tool. In the Dot, connect the plugin and calendar, then ask: “Monitor AfterHello's completed conversations. Identify meeting plans, check my calendar, and propose a time. Ask me before creating events or sending invitations.” The endpoint has no calendar-write capability; the Dot's connected calendar and user instructions govern scheduling.

ChatGPT supplies the callback URL and signing secret during subscription. The service verifies a signed challenge, pins public callback IPs with TLS hostname verification, rejects redirects/private destinations, and signs event deliveries using Standard Webhooks. It persists subscriptions and bounded retry jobs in the ignored `afterhello-events.sqlite`. MCP token rotation disables old subscriptions. No history is sent on first installation. Newly saved conversation summaries and conversation-scoped wearer promises are eligible; raw dialogue, email addresses, phone numbers and app credentials are excluded. A `2xx` callback means received, not that an event was booked. Check the read-only status tool for active subscriptions and delivery counts.

Tests: `.venv/bin/python -m unittest tests.test_dot_events -q`. A real Dot subscription and calendar action still require connecting the plugin in ChatGPT.


### Automatic room follow-ups through a Dot

Set `AFTERHELLO_ROOM_SOURCE` to the shared room SQLite database and `AFTERHELLO_ROOM_PARTICIPANT_ID` to this wearer's registered ID, then restart only `remember.dot_events`. Rescan the AfterHello plugin so ChatGPT discovers `room.connection.created`, `claim_room_booking`, and `complete_room_booking`. The room event includes the two names, introduction/match reasons, timezone, and a stable `booking_key`. Plain meetings, simulated participants, and pairs that do not involve the configured wearer are excluded. Existing connections are marked seen when enabling this integration; no old introductions are automatically booked. A later match for an already introduced pair does not create a second scheduling request.

Give the Dot this instruction:

> Replace conversation-based automatic scheduling with AfterHello's `room.connection.created` event. Subscribe to that event and stop automatic calendar creation from `conversation.completed`. For each new room pair, call `claim_room_booking` with its `booking_key`. Proceed only if `granted` is true. Check my calendar and book the next free 30-minute weekday slot between 9 AM and 5 PM in the event's timezone, adding Google Meet. Put the booking key in the calendar description. Create it on my calendar without guests unless a verified attendee email is available. Do not guess an email. Call `complete_room_booking` with the booking key, actual calendar event ID, and Google Meet URL. Then notify me with the time and shareable link. Do not ask for approval first. If a claim already exists, reuse the recorded meeting or reconcile it against the calendar by booking key; never create a second event. If creation fails or its outcome is uncertain, report that instead of claiming success.

The webhook is at-least-once delivery; the claim/complete protocol prevents cooperating workers from booking twice. A failed or interrupted booking stays claimed for reconciliation (it is not automatically retried as a fresh booking). Callback receipt is separate from calendar completion. The actual booking runs in the subscribed Dot using its connected calendar, not in AfterHello's backend. Tests exercise pair deduplication across restart, simultaneous claims, event-scoped subscriptions, real/simulated participant filtering, and cancellation of deleted pairs.
