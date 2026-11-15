"""Run three actual kit simulator bands against one local room server."""
import argparse
import threading
import time

import uvicorn

from .band import BandSession
from .extract import HeuristicExtractor
from .room import create_room_app
from .room_bridge import RoomBridge
from .room_matching import match_room
from .session import Session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8003)
    parser.add_argument('--lan', action='store_true', help='Allow phones on the same Wi-Fi to view the demo')
    args = parser.parse_args()
    app = create_room_app(matcher=lambda participants, speaker_id, card, marked=False:
                          match_room(participants, speaker_id, card, marked=marked, use_model=False))
    server = uvicorn.Server(uvicorn.Config(app, host='0.0.0.0' if args.lan else '127.0.0.1', port=args.port, access_log=False, log_level='warning'))
    people = [
        {'id':'alex','name':'Alex Chen','needs':['FPGA internship'],'simulated':True},
        {'id':'priya','name':'Priya Shah','company':'Circuit Works','simulated':True},
        {'id':'sam','name':'Sam Rivera','company':'Circuit Works','offers':['Hiring FPGA interns'],'simulated':True},
    ]
    registered = [app.state.room.register(person) for person in people]
    print('Room projector: http://127.0.0.1:%d/?token=%s' % (args.port, app.state.projector_token), flush=True)
    resources = []
    def simulate():
        while not server.started and not server.should_exit:
            time.sleep(.05)
        if server.should_exit:
            return
        try:
            for index, registration in enumerate(registered):
                session = Session(':memory:', HeuristicExtractor())
                band = BandSession(session, number=101+index, sim=True)
                band.start()
                bridge = RoomBridge(session, 'http://127.0.0.1:%d' % args.port, registration['token'], alert=band.room_alert)
                session.on_contact_ready = bridge.enqueue
                bridge.start()
                resources.append((session, band, bridge))
            alex = resources[0][0]
            alex.start()
            alex.hear("I'm Priya Shah. You should meet Sam Rivera; he is hiring FPGA interns.", speaker='1')
            alex.mark()
            alex.end()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                states = [entry[0].state()['status'].get('room', {}) for entry in resources]
                received = [set(item['kind'] for item in state.get('alerts', [])) for state in states]
                acknowledged = all(not app.state.room.alerts(name)['alerts'] for name in ('alex', 'sam'))
                if (received[0] == {'intro', 'match'} and received[2] == {'intro', 'match'}
                        and not received[1] and acknowledged
                        and all(state.get('queued') == 0 for state in states)):
                    print('Verified: meeting, introduction and match; private alerts reached Alex and Sam only; simulator LEDs succeeded and alerts acknowledged.', flush=True)
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('Room delivery verification timed out')
        except Exception as error:
            print('Room demo could not start: %s' % type(error).__name__, flush=True)
            server.should_exit = True
    worker = threading.Thread(target=simulate, daemon=True)
    worker.start()
    try:
        server.run()
    finally:
        worker.join(timeout=5)
        for session, band, bridge in resources:
            bridge.close()
            band.shutdown()
            session.close()


if __name__ == '__main__':
    main()
