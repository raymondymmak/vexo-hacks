# Remember

You remember people. They remember you.

Remember is a hands-free notebook for back-to-back conversations, such as a career fair. A Vexo Open wristband hears the room. Pipecat sends that audio to Deepgram. When the conversation ends, one card is saved: who they are, what they are working on, what they care about, what you promised, and the moments you tapped to keep. The next time you meet them, the page finds the card.

The band has a microphone, a motion sensor, and one light. It has no screen and no vibration. Double-tap starts a new person. A single tap marks a moment. The page is the screen.

Audio always goes through a Pipecat pipeline:

`band or clip (PCM) → BandAudioTransport → DeepgramSTTService → RememberProcessor → cards`

`DeepgramSTTService` is Pipecat's Deepgram speech-to-text service, model `nova-3-general`, with interim results, speaker diarization, punctuation, and `mip_opt_out` so Deepgram does not keep the audio for training. With no API key, `MockDeepgramSTTService` sits in that same slot and the tests never call the network.

## Set up

From this folder, on Python 3.11 or newer (3.12 is what this was run on). Pipecat 1.16 does not support Python 3.9 or 3.10.

    sh scripts/setup.sh
    python3 -m venv .venv
    source .venv/bin/activate
    python3 -m pip install -r requirements.txt

`scripts/setup.sh` clones the Vexo Open kit into `vexo-open-kit/` and leaves it unchanged. That folder is gitignored. On Windows, use `python` instead of `python3`.

## (a) Pretend band, no API keys

    python3 -m remember --sim --demo

Open <http://127.0.0.1:8000>. The pretend band connects, the scripted career fair plays through the card builder, and the light flashes green as each card is saved. Nothing is sent to Deepgram or OpenAI. The page lets you type a line, mark a moment, end the conversation, search, play the demo again, or reset.

The same pipeline will also take a recording, still with no key. The file is PCM into Pipecat. The offline reader then speaks the scripted fair (it does not transcribe the file):

    python3 -m remember --clip vexo-open-kit/dashboard/speech/sample-speech.opus --once

A 16 kHz mono 16-bit `.wav` works the same way. Add `--sim` if you also want the pretend band connected while a clip plays.

To print the demo cards and exit, without the page:

    python3 -m remember --demo --once

## (b) Your band, on a Mac, with keys

Use the number written on your band's bag. This program never guesses it and never scans for another band. Close the kit dashboard first: the band takes one Bluetooth connection.

Tell the people near you before the microphone goes on. The light turns solid blue, and everything it hears is sent to Deepgram through Pipecat.

Create `.env` yourself from the example. Do not commit it, and do not paste the keys into a chat.

    cp env.example .env

Put your team's `DEEPGRAM_API_KEY` and `OPENAI_API_KEY` on those two lines. Then:

    python3 -m pip install bleak
    python3 -m remember NUMBER

Double-tap the band so it blinks blue, then start the command. Double-tap again when you meet someone. A single tap marks the line you want to keep. Double-tap for the next person, or press End conversation on the page. After a card is saved the light goes green for a moment. The microphone is paused for that, because the band refuses the light while the microphone is on.

`--sim` with both keys set plays a clip through the real Deepgram service instead of the offline reader:

    python3 -m remember --sim --clip vexo-open-kit/dashboard/speech/sample-speech.opus

## Gestures

| Gesture | What it does |
|---|---|
| Double-tap | Close the current person, if they said anything, and start the next one |
| Single tap | Keep the words around that moment on the card |
| End conversation | Save the current person and wait |
| Type a line | Same pipeline as speech, without the microphone |

The first words also open a conversation if the double-tap comes a moment late.

## Tests

    python3 -m unittest discover -s tests -v

The tests use the pretend band and `MockDeepgramSTTService`. They do not need API keys, Bluetooth, or a network call. `REMEMBER_STT=mock` and `REMEMBER_EXTRACTOR=builtin` force that even if a key is set in the environment.

## What the code uses

Pipecat 1.16.0 (`pipecat-ai[deepgram]==1.16.0`):

- `Pipeline`, `PipelineWorker`, `PipelineParams`, `WorkerRunner`
- `BaseTransport`, `BaseInputTransport`, `TransportParams`
- `InputAudioRawFrame`, `TranscriptionFrame`, `InterimTranscriptionFrame`, `TranscriptionSpeaker`
- `DeepgramSTTService` and `DeepgramSTTService.Settings`
- `FrameProcessor` for `MockDeepgramSTTService` and `RememberProcessor`

The band library is the kit's `vexo_open.sync.Band`. Opus packets are decoded to 16 kHz PCM with `PcmDecoder` and pushed into the transport. Cards are one OpenAI structured-output call per conversation through the kit's `ai.extract` when `OPENAI_API_KEY` is set, and a built-in reader otherwise. People, conversations, and promises are in SQLite. The page is FastAPI with server-sent events.

## Layout

    remember/          this program
    vexo-open-kit/     the kit, cloned by scripts/setup.sh, not committed
    .env               your keys, not committed
    env.example        the empty form
