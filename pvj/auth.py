"""Device pairing and access control.

* A 4-digit PIN (shown on the box) pairs a phone or tablet and yields a token.
* Tokens are random 192-bit values; only their SHA-256 is stored.
* Roles: view (read only) < live (play and mix) < full (everything).
* The PIN is stored as a salted scrypt hash. Because it is short, guessing is
  throttled per client and globally, and comparisons are constant-time.
"""

import hashlib
import hmac
import secrets
import time

ROLES = {"view": 1, "live": 2, "full": 3}
PIN_LENGTH = 4
PER_CLIENT_FAILS, PER_CLIENT_WINDOW = 5, 60.0
GLOBAL_FAILS, GLOBAL_WINDOW = 20, 600.0
LOCKOUT_SECONDS = 60.0


class AuthError(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def generate_pin():
    return "%0*d" % (PIN_LENGTH, secrets.randbelow(10 ** PIN_LENGTH))


def _scrypt(secret, salt):
    return hashlib.scrypt(secret.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, settings, clock=time.monotonic, now=time.time, rotate_on_start=False):
        self.settings = settings
        self._clock = clock
        self._now = now
        self._fails = {}        # client -> [timestamps]
        self._global_fails = []
        self._locked_until = {}  # client or "*" -> time
        self.last_seen = {}
        if rotate_on_start or not settings.data["auth"].get("pin_hash"):
            self._new_pin()

    # --- PIN -----------------------------------------------------------
    def _new_pin(self, pin=None):
        pin = pin or generate_pin()
        salt = secrets.token_bytes(16)
        self.settings.data["auth"] = {"pin_hash": _scrypt(pin, salt).hex(), "pin_salt": salt.hex()}
        self.settings.save()
        self.current_pin = pin  # only known in memory until shown; not stored in clear
        return pin

    def set_pin(self, pin):
        if not (isinstance(pin, str) and pin.isdigit() and len(pin) == PIN_LENGTH):
            raise AuthError("PIN must be %d digits" % PIN_LENGTH)
        return self._new_pin(pin)

    def rotate_pin(self):
        return self._new_pin()

    def _check_pin(self, pin):
        auth = self.settings.data["auth"]
        expected = bytes.fromhex(auth["pin_hash"])
        given = _scrypt(pin if isinstance(pin, str) else "", bytes.fromhex(auth["pin_salt"]))
        return hmac.compare_digest(expected, given)

    # --- throttling ----------------------------------------------------
    def _locked(self, client):
        t = self._clock()
        for key in (client, "*"):
            until = self._locked_until.get(key, 0)
            if until > t:
                return until - t
        return 0

    def _record_fail(self, client):
        t = self._clock()
        fails = [x for x in self._fails.get(client, []) if t - x < PER_CLIENT_WINDOW] + [t]
        self._fails[client] = fails
        if len(fails) >= PER_CLIENT_FAILS:
            self._locked_until[client] = t + LOCKOUT_SECONDS
            self._fails[client] = []
        self._global_fails = [x for x in self._global_fails if t - x < GLOBAL_WINDOW] + [t]
        if len(self._global_fails) >= GLOBAL_FAILS:
            self._locked_until["*"] = t + LOCKOUT_SECONDS * 5
            self._global_fails = []

    # --- pairing and tokens --------------------------------------------
    def pair(self, pin, name, client="?"):
        wait = self._locked(client)
        if wait:
            raise AuthError("too many attempts", retry_after=int(wait) + 1)
        if not self._check_pin(pin):
            self._record_fail(client)
            raise AuthError("wrong PIN")
        self._fails.pop(client, None)
        return self._add_device(name, "full")

    def invite(self, name, role):
        if role not in ("view", "live"):
            raise AuthError("invites are for view or live access")
        return self._add_device(name, role)

    def _add_device(self, name, role):
        token = secrets.token_urlsafe(24)
        device = {"id": secrets.token_hex(4), "name": str(name or "device")[:40], "role": role,
                  "token_hash": _token_hash(token), "created": int(self._now())}
        self.settings.data["devices"].append(device)
        self.settings.save()
        return token, self._public(device)

    def authenticate(self, token):
        if not isinstance(token, str) or not token:
            return None
        h = _token_hash(token)
        found = None
        for d in self.settings.data["devices"]:
            if hmac.compare_digest(d["token_hash"], h):
                found = d
        if found:
            self.last_seen[found["id"]] = int(self._now())
            return self._public(found)
        return None

    def revoke(self, device_id):
        before = len(self.settings.data["devices"])
        self.settings.data["devices"] = [d for d in self.settings.data["devices"] if d["id"] != device_id]
        changed = len(self.settings.data["devices"]) != before
        if changed:
            self.settings.save()
        return changed

    def revoke_all(self):
        self.settings.data["devices"] = []
        self.settings.save()

    def list_devices(self):
        return [dict(self._public(d), last_seen=self.last_seen.get(d["id"])) for d in self.settings.data["devices"]]

    @staticmethod
    def _public(device):
        return {k: device[k] for k in ("id", "name", "role", "created")}

    @staticmethod
    def allows(device, needed):
        return bool(device) and ROLES[device["role"]] >= ROLES[needed]
