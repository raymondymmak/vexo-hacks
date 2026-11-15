"""Register explicitly selected saved contacts for room recommendations.

Example: python scripts/register_room_contacts.py --contact 14:raymond --contact 24:timothy-vexo19 --wearer timothy-vexo19
Only professional profile fields are shared; never transcripts, phone or email.
"""
import argparse
import os
from pathlib import Path
import sys
import httpx
from dotenv import set_key
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from remember.kitpath import load_dotenv
from remember.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contact', action='append', required=True, help='Saved contact ID:room participant ID')
    parser.add_argument('--wearer', help='Room participant ID associated with this laptop; refreshes its token')
    parser.add_argument('--db', default=str(ROOT / 'remember.sqlite'))
    args = parser.parse_args()
    load_dotenv()
    url = os.environ['REMEMBER_ROOM_URL'].rstrip('/')
    headers = {'Authorization': 'Bearer ' + os.environ['REMEMBER_ROOM_ADMIN_TOKEN']}
    store = Store(args.db)
    with httpx.Client(timeout=5, follow_redirects=False) as client:
        response = client.get(url + '/api/state', headers=headers)
        response.raise_for_status()
        known = {p['id']: p for p in response.json()['participants']}
        # Validate every selection before changing any registrations.
        selected = []
        for value in args.contact:
            contact_id, participant_id = value.split(':', 1)
            person = store.get(int(contact_id))
            if not person or person['name'] == 'Someone you met':
                raise ValueError('Choose a named saved contact')
            profile = {k: str(person.get(k) or '') for k in ('name', 'company', 'role', 'working_on', 'summary')}
            profile.update(id=participant_id, simulated=known.get(participant_id, {}).get('simulated', False))
            if participant_id in known:
                profile['name'] = known[participant_id]['name']
            selected.append(profile)
        for profile in selected:
            response = client.post(url + '/api/participants', headers=headers, json=profile)
            response.raise_for_status()
            if profile['id'] == args.wearer:
                set_key(str(ROOT / '.env'), 'REMEMBER_ROOM_TOKEN', response.json()['token'])
                (ROOT / '.env').chmod(0o600)
            print('Registered saved profile: ' + profile['name'])
    print('Restart the band backend if its participant token changed.')


if __name__ == '__main__':
    main()
