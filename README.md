# Remember

Meet someone. Remember them on your phone.

One Vexo Open band captures a conversation through Pipecat and Deepgram. End the conversation to create a contact with their name, company, role, contact details, notes, and attributed promises. With automatic delivery configured, Remember saves the contact to iCloud through CardDAV and sends an ntfy notification. A downloadable vCard is always available as a fallback.

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

For notifications, install/open ntfy on your phone and subscribe to your chosen topic yourself. Set `NTFY_URL` to its full HTTPS topic URL, and set `NTFY_TOKEN` when your server/topic requires authentication. Remember publishes to that topic; it does not subscribe the phone automatically.

Optionally set `REMEMBER_PUBLIC_URL` to the base URL where your running Remember instance is reachable from the phone. Notifications can then include a contact-scoped vCard fallback link. This setting does not expose or host the server for you. Use your own reachable address; a laptop's `localhost` address will not work on a phone. Fallback links require HTTPS, expire after 15 minutes, and stop working after a server restart. They are attached only if the iCloud save fails.

Social lookup is best-effort and runs only in automatic delivery mode. `REMEMBER_SOCIAL_LOOKUP=1` enables it (the default in that mode); `0` disables it. It uses name and company to seek a supported public profile with source evidence, and leaves the URL empty when uncertain or unavailable. `REMEMBER_SOCIAL_MODEL` defaults to `gpt-4.1-mini` and is configurable.

The demo target is fast contact-to-phone delivery, not a guaranteed ten-second result. Displayed elapsed time starts when capture is ended and includes extraction and background enrichment/delivery; it excludes later iPhone Contacts sync. A successful server write or notification publish is not proof the phone has displayed it. Failed delivery leaves the local card and manual vCard download available.

## Data and tests

New captures keep raw transcript text in memory for the active conversation and extraction. New raw transcripts are not persisted; saved contacts, generated summaries, attributed promises, and delivery status remain in SQLite. Raw tapped quotes are discarded too. Existing databases are migrated additively; historical transcript rows are not erased. Use the app's reset only when you intend to delete all saved contacts and conversations.

Audio flow:

`band or clip PCM → Pipecat transport → Deepgram speech-to-text → session → contact extraction → local save → optional enrichment/delivery`

The UI is FastAPI plus server-sent events. Local saving and vCard export work when delivery is disabled or unavailable.

```sh
REMEMBER_STT=mock REMEMBER_EXTRACTOR=builtin .venv/bin/python -m unittest discover -s tests -v
```

Tests use simulated input and mocked provider responses. Real-band audio, handshake detection, iCloud credentials, notification subscriptions, and final iPhone sync require separate hardware/account validation.
