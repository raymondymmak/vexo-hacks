"""Bounded background contact enrichment and delivery after local saving."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import os
import threading
import time
from .delivery import DeliveryService
from .socials import lookup_social, confirmed_profile
from .profile_photo import fetch_profile_photo
from .vcard import render_contact


def contact_key(secret, person, expires=None):
    expires = int(time.time()) + 900 if expires is None else int(expires)
    message = str(person['contact_uid']) + ':' + str(expires)
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return str(expires) + '.' + signature


def valid_contact_key(secret, person, key):
    try:
        expires = int(key.split('.', 1)[0])
        if not int(time.time()) <= expires <= int(time.time()) + 900:
            return False
        expected = contact_key(secret, person, expires)
        return hmac.compare_digest(key.encode(), expected.encode())
    except (ValueError, TypeError, AttributeError):
        return False


class ContactFlow:
    def __init__(self, session, *, offline=False, secret='', service=None, lookup=lookup_social, photo_lookup=None):
        self.session = session
        self.offline = offline
        self.secret = secret
        self.service = service or DeliveryService.from_env()
        self.lookup = lookup
        self.photo_lookup = photo_lookup or fetch_profile_photo
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='contact-delivery')
        self.futures = set()
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = False

    def enqueue(self, person):
        person = dict(person)
        with self._lock:
            if self._closed:
                return
            generation = self._generation
        delivery = {'social': {'status': 'disabled' if self.offline else 'pending'},
                    'icloud': {'status': 'disabled' if self.offline else 'pending'},
                    'ntfy': {'status': 'disabled' if self.offline else 'pending'}}
        if not self._publish(person, delivery, generation):
            return
        if not self.offline:
            with self._lock:
                if self._closed or generation != self._generation:
                    return
                future = self.executor.submit(self._deliver, person, generation)
                self.futures.add(future)
                future.add_done_callback(self._finished)

    def _finished(self, future):
        with self._lock:
            self.futures.discard(future)

    def cancel_pending(self):
        """Invalidate prior jobs on reset; a provider request already sent cannot be recalled."""
        with self._lock:
            self._generation += 1
            for future in tuple(self.futures):
                future.cancel()

    def _current(self, person, generation):
        with self._lock:
            if generation != self._generation:
                return None
            current = self.session.store.get(person['id'])
            if current and current['contact_uid'] == person['contact_uid']:
                return current
        return None

    def _publish(self, person, delivery, generation, social_url=None):
        with self._lock:
            if generation != self._generation:
                return None
            updated = self.session.store.set_delivery(
                person['id'], delivery, social_url,
                contact_uid=person['contact_uid'], original_social_url=person.get('social_url', ''))
        if updated:
            self.session._publish({'type': 'delivery', 'person': updated, 'status': self.session.state()['status']})
        return updated

    def _deliver(self, person, generation):
        started = person.get('_finished_at', time.monotonic())
        if not self._current(person, generation):
            return
        delivery = {'social': {'status': 'pending'}, 'icloud': {'status': 'pending'}, 'ntfy': {'status': 'pending'}}
        def progress(stage, result):
            delivery[stage] = result
            self._publish(person, dict(delivery), generation)
        social = ''
        try:
            current = self._current(person, generation)
            if not current:
                return
            unnamed = (current.get('name') or '').strip().casefold() in ('', 'someone you met', 'unknown', 'someone')
            if unnamed and not current.get('email') and not current.get('phone'):
                delivery = {'social': {'status': 'skipped', 'detail': 'Contact identity is missing.'}, 'icloud': {'status': 'skipped', 'error': 'Add a name, email, or phone before delivery.'},
                            'ntfy': {'status': 'skipped'}}
                self._publish(person, delivery, generation)
                return
            if os.environ.get('REMEMBER_SOCIAL_LOOKUP', '1') == '1':
                progress("social", {"status": "active"})
                lookup_errors = []
                rejections = []
                remembered = confirmed_profile(current, self.session.store.list_people()) if self.lookup is lookup_social else ''
                provider = ['saved' if remembered else '']
                def provider_started(name):
                    provider[0] = name
                    progress('social', {'status': 'active', 'provider': name})
                social = remembered or (self.lookup(current, on_error=lookup_errors.append, on_rejection=rejections.append, on_provider=provider_started) if self.lookup is lookup_social else self.lookup(current))
                progress("social", {"provider": provider[0], "status": "error", "error": lookup_errors[0]} if lookup_errors else {"provider": provider[0], "status": "done" if social else "skipped", "detail": ("Matched a previously confirmed contact using two shared identity clues." if remembered else "LinkedIn match found in search sources.") if social else (rejections[-1] if rejections else "No confident LinkedIn match returned.")})
            else:
                progress("social", {"status": "disabled"})
            # Re-read after enrichment so a user edit wins over the original
            # extraction snapshot, and a reset prevents the provider send.
            current = self._current(person, generation)
            if not current:
                return
            if social and current.get('social_url', '') == person.get('social_url', ''):
                current = dict(current, social_url=social)
            # Persist the accepted profile before looking up its optional portrait.
            self._publish(person, dict(delivery), generation, social)
            profile = current.get('social_url', '')
            if profile:
                try:
                    photo = self.photo_lookup(profile)
                    with self._lock:
                        latest = self._current(person, generation)
                        if photo and latest and latest.get('social_url') == profile:
                            self.session.store.set_photo(person['id'], photo, person['contact_uid'])
                except Exception:
                    pass  # Public portraits are optional; delivery still proceeds.
            current = self._current(person, generation)
            if not current:
                return
            base = os.environ.get('REMEMBER_PUBLIC_URL', '').rstrip('/')
            fallback = (base + '/contact/%d.vcf?key=%s' % (current['id'], contact_key(self.secret, current))) if base.startswith("https://") and self.secret else None
            kwargs = {"progress": progress} if isinstance(self.service, DeliveryService) else {}
            result = self.service.deliver(current, render_contact(current), fallback_url=fallback, **kwargs)
            delivery.update({'icloud': result.get('icloud', {}), 'ntfy': result.get('notification', result.get('ntfy', {}))})
        except Exception:
            delivery = {'icloud': {'status': 'error', 'error': 'Contact delivery failed; download the contact as a fallback.'},
                        'ntfy': {'status': 'error', 'error': 'Notification was not confirmed.'}}
        delivery['elapsed_seconds'] = round(time.monotonic() - started, 2)
        self._publish(person, delivery, generation, social)

    def close(self):
        with self._lock:
            self._closed = True
        self.executor.shutdown(wait=True)
