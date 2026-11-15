"""AfterHello: you remember people. They remember you.

    python3 -m remember --sim --demo
    python3 -m remember NUMBER
"""

import argparse
import os
import sys
import threading

from .clips import load_pcm, play_pcm
from .demo import SCRIPTS, play
from .extract import HeuristicExtractor, make_extractor
from .kitpath import load_dotenv, plain
from .session import Session
from .voice import PCM_FRAME_BYTES, deepgram_key


def band_number(text: str) -> int:
    """The number on the band's bag: 1 to 999, or the name Vexo-HACK-N."""
    raw = text.strip()
    prefix = "vexo-hack-"
    if raw.lower().startswith(prefix):
        raw = raw[len(prefix):]
    if not raw.isascii() or not raw.isdigit():
        raise argparse.ArgumentTypeError("Type the number on your band's bag, from 1 to 999.")
    number = int(raw)
    if not 1 <= number <= 999:
        raise argparse.ArgumentTypeError("Type the number on your band's bag, from 1 to 999.")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="remember",
        description="Hands-free notes for the people you meet, from a Vexo Open band.",
    )
    parser.add_argument("number", nargs="?", type=band_number, help="the number on your band's bag")
    parser.add_argument("--sim", action="store_true", help="use the pretend band (no Bluetooth)")
    parser.add_argument("--demo", action="store_true", help="play the scripted career-fair conversations")
    parser.add_argument("--say", default="", help="one typed conversation, instead of the microphone")
    parser.add_argument("--clip", default="", help="a .wav or .opus recording to send through the pipeline")
    parser.add_argument("--once", action="store_true", help="run the demo or the clip, print the cards, and exit")
    parser.add_argument("--pace", type=float, default=None, help="seconds between demo lines or clip chunks")
    parser.add_argument("--db", default="remember.sqlite", help="SQLite file")
    parser.add_argument("--port", type=int, default=8000, help="web page port, bound to 127.0.0.1")
    parser.add_argument("--lan", action="store_true", help="serve paired phone page on the local network")
    parser.add_argument("--handshake", action="store_true", help="enable experimental motion handshake detection")
    args = parser.parse_args(argv)
    if args.number is None and not args.sim and not args.demo and not args.say and not args.clip and args.once:
        parser.error("--once needs --demo, --say, or --clip")
    if args.clip and args.number is not None and not args.sim:
        parser.error("play a clip with --sim, or without a band number. A real band uses its own microphone.")
    if args.demo and args.clip:
        parser.error("use either --demo or --clip")
    return args


def clip_events():
    """The scripted fair, as events the offline reader emits while a clip plays."""
    events = []
    for script in SCRIPTS:
        events.append(("double",))
        taps = set(script.get("tap_after") or [])
        for index, line in enumerate(script["lines"]):
            events.append(("text", line))
            if index in taps:
                events.append(("tap",))
        events.append(("double",))
    return events


def _settle(session, timeout: float = 5) -> None:
    """Wait until the pipeline has finished saving, or until timeout."""
    import time
    last = None
    stable = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        count = session.store.count()
        phase = session.state()["status"]["phase"]
        if count == last and phase == "idle" and count > 0:
            stable += 1
            if stable >= 3:
                return
        else:
            stable = 0
            last = count
        time.sleep(0.05)


def main(argv=None) -> int:
    args = parse_args(argv)
    load_dotenv()
    if args.demo or os.environ.get("REMEMBER_EXTRACTOR", "").strip().lower() == "builtin":
        extractor = HeuristicExtractor()
    else:
        extractor = make_extractor()
    session = Session(args.db, extractor=extractor)
    key = deepgram_key()
    live = bool(key) and (args.number is not None or args.sim or bool(args.clip)) and not args.demo
    # A clip with no key still flows through Pipecat; the mock supplies the script.
    needs_pipeline = args.number is not None or args.sim or bool(args.clip)
    import secrets
    download_secret = secrets.token_urlsafe(32)
    from .contact_flow import ContactFlow
    flow = ContactFlow(session, offline=args.demo or not live, secret=download_secret)
    session.on_contact_ready = flow.enqueue
    session.on_reset = flow.cancel_pending
    pipeline = None
    band = None
    room = None
    pace = args.pace
    if pace is None:
        pace = 0.0 if args.once else 0.45

    try:
        if needs_pipeline:
            from .voice import VoicePipeline
            events = clip_events() if args.clip and not live else None
            pipeline = VoicePipeline(session, live=live, events=events)
            pipeline.start()
            which = "Deepgram nova-3-general" if pipeline.live else "the offline reader"
            print("Audio pipeline: Pipecat, %s." % which)

        if args.number is not None or args.sim:
            from .band import BandSession
            listen = bool(pipeline and pipeline.live and not args.demo)
            replay = args.clip if listen else ""
            band = BandSession(
                session,
                number=args.number,
                sim=args.sim,
                pipeline=pipeline,
                replay=replay,
                listen=listen,
                handshake=args.handshake,
            )
            band.start()

        room_url = os.environ.get("REMEMBER_ROOM_URL", "").strip()
        room_token = os.environ.get("REMEMBER_ROOM_TOKEN", "").strip()
        if room_url and room_token and not args.demo:
            from .room_bridge import RoomBridge
            room = RoomBridge(session, room_url, room_token, db_path=args.db,
                              alert=lambda item: band.room_alert(item) if band else True)
            def contact_ready(person):
                flow.enqueue(person)
                room.enqueue(person)
            def reset_pending():
                flow.cancel_pending()
                room.reset()
            session.on_contact_ready = contact_ready
            session.on_reset = reset_pending
            room.start()

        if args.say:
            for line in [part.strip() for part in args.say.splitlines() if part.strip()] or [args.say.strip()]:
                if line:
                    session.hear(line, True)
            session.end()

        demo_thread = None
        if args.demo:
            def run_demo():
                play(session, pace=pace)
            if args.once:
                run_demo()
            else:
                demo_thread = threading.Thread(target=run_demo, name="remember-demo", daemon=True)
                demo_thread.start()

        clip_direct = bool(args.clip) and not listen_band(band)
        if clip_direct:
            if pipeline.live:
                session.start()
            def run_clip():
                import time
                events = clip_events()
                try:
                    pcm = load_pcm(args.clip)
                except Exception as error:
                    print("Problem: %s" % error)
                    print("Sending silence through the pipeline so the offline reader can still speak.")
                    pcm = b""
                if not pipeline.live:
                    need = pipeline.bytes_per_line * len(events)
                    if len(pcm) < need:
                        pcm = pcm + (b"\x00" * (need - len(pcm)))
                chunk = pipeline.bytes_per_line if not pipeline.live else PCM_FRAME_BYTES
                play_pcm(pipeline, pcm, chunk=chunk, pace=0.0 if args.once else min(pace, 0.02))
                deadline = time.time() + 5
                if not pipeline.live:
                    while time.time() < deadline:
                        people = session.state()["people"]
                        talks = sum(person["conversation_count"] for person in people)
                        if talks >= 3:
                            break
                        time.sleep(0.02)
                else:
                    time.sleep(0.5)
                session.end()

            if args.once:
                run_clip()
            else:
                threading.Thread(target=run_clip, name="remember-clip", daemon=True).start()

        if args.once:
            if args.clip:
                _settle(session)
            for person in session.state()["people"]:
                print(plain(person["name"]))
            return 0

        def play_demo_background():
            if getattr(play_demo_background, "busy", False):
                return False
            play_demo_background.busy = True

            def run():
                try:
                    play(session, pace=0.45 if args.pace is None else args.pace)
                finally:
                    play_demo_background.busy = False

            threading.Thread(target=run, name="remember-demo", daemon=True).start()
            return True

        from .web import create_app
        import uvicorn

        import secrets
        import socket
        token = os.environ.get("REMEMBER_PAIRING_TOKEN", "").strip() or secrets.token_urlsafe(24)
        app = create_app(session, play_demo=None if live else play_demo_background, pairing_token=token, download_secret=download_secret)
        if args.lan:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    probe.connect(("192.0.2.1", 9))
                    address = probe.getsockname()[0]
            except OSError:
                address = socket.gethostbyname(socket.gethostname())
            print("Phone (same Wi-Fi): http://%s:%d/?token=%s" % (address, args.port, token))
        print("Open http://127.0.0.1:%d%s" % (args.port, "/?token=" + token if token else ""))
        print("Double-tap to start or end capture. A single tap marks a moment. "
              "End the conversation from the page when the room goes quiet.")
        uvicorn.run(app, host="0.0.0.0" if args.lan else "127.0.0.1", port=args.port, log_level="warning", access_log=False)
        return 0
    finally:
        if room is not None:
            room.close()
        if band is not None:
            band.shutdown()
        if pipeline is not None:
            pipeline.stop()
        flow.close()
        session.close()


def listen_band(band) -> bool:
    return band is not None and getattr(band, "listen", False)


if __name__ == "__main__":
    sys.exit(main())
