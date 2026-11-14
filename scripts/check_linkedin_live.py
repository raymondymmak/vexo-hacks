"""Opt-in paid-provider smoke check: python scripts/check_linkedin_live.py [--case ID]."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from remember.kitpath import load_dotenv
from remember.socials import lookup_social


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['raymond_mak', 'timothy_do'])
    args = parser.parse_args()
    load_dotenv()
    cases = json.loads((ROOT / 'tests/fixtures/linkedin_people.json').read_text())
    failed = False
    for case in cases:
        if args.case and args.case != case['id']:
            continue
        errors = []
        started = time.monotonic()
        actual = lookup_social(case['person'], on_error=errors.append)
        passed = actual.rstrip('/') == case['expected_url'].rstrip('/')
        failed |= not passed
        print(json.dumps({'case': case['id'], 'passed': passed, 'seconds': round(time.monotonic() - started, 2),
                          'expected': case['expected_url'], 'actual': actual, 'errors': errors}), flush=True)
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
