"""Hosted sign-in. Supabase Auth over its REST API (no SDK): a magic link emailed to an invited address, a token_hash the
server verifies, and a session cookie holding Supabase's own JWT. The JWT is checked here against Supabase's public keys.
Nothing here reads or writes the database: invites and consent live in db.py."""
import hashlib
import hmac
import time

import httpx
import jwt

COOKIE = "jf_session"
AUDIENCE = "authenticated"
JWKS_MAX_AGE = 3600


class AuthError(Exception):
    """Sign-in failed. The message is safe to log; it never holds a token."""


def user_hash(user_id):
    """A short stable hash for logs: enough to follow one user's events, not enough to identify them."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:12]


class SupabaseAuth:
    def __init__(self, url, anon_key, client=None):
        self.url = url.rstrip("/")
        self.issuer = f"{self.url}/auth/v1"
        self.anon_key = anon_key
        self.client = client or httpx.Client(timeout=10)
        self._keys, self._keys_at = {}, 0.0

    def _headers(self):
        return {"apikey": self.anon_key, "Content-Type": "application/json"}

    def send_link(self, email, redirect_to):
        """Ask Supabase to email a sign-in link. Supabase is set not to create users: only invited addresses can get one."""
        r = self.client.post(f"{self.issuer}/otp", headers=self._headers(),
                             json={"email": email, "create_user": False, "email_redirect_to": redirect_to})
        if r.status_code >= 500:
            raise AuthError(f"sign-in service error {r.status_code}")

    def verify(self, token_hash):
        """Exchange the emailed token_hash for a session. Returns the access token (a JWT)."""
        r = self.client.post(f"{self.issuer}/verify", headers=self._headers(),
                             json={"type": "magiclink", "token_hash": token_hash})
        if r.status_code != 200:
            raise AuthError("the sign-in link is not valid or has expired")
        token = (r.json() or {}).get("access_token")
        if not token:
            raise AuthError("no session returned")
        return token

    def _key_for(self, kid):
        stale = time.time() - self._keys_at > JWKS_MAX_AGE
        if kid not in self._keys or stale:
            r = self.client.get(f"{self.issuer}/.well-known/jwks.json", headers={"apikey": self.anon_key})
            if r.status_code != 200:
                raise AuthError("could not fetch signing keys")
            self._keys = {k["kid"]: jwt.PyJWK(k) for k in r.json().get("keys", []) if "kid" in k}
            self._keys_at = time.time()
        if kid not in self._keys:
            raise AuthError("unknown signing key")
        return self._keys[kid].key

    def decode(self, token):
        """The verified claims of an access token: signature, expiry, issuer and audience all checked.
        Raises AuthError for anything else (forged, expired, wrong issuer, no subject)."""
        try:
            header = jwt.get_unverified_header(token)
            alg = header.get("alg")
            if alg not in ("ES256", "RS256", "EdDSA"):          # never "none", never a shared-secret algorithm
                raise AuthError("unsupported token algorithm")
            claims = jwt.decode(token, self._key_for(header.get("kid")), algorithms=[alg], audience=AUDIENCE,
                                issuer=self.issuer, options={"require": ["exp", "sub", "iss", "aud"]})
        except jwt.PyJWTError as e:
            raise AuthError(f"invalid session: {type(e).__name__}") from None
        if not claims.get("email"):
            raise AuthError("session has no email")
        return claims

    def delete_user(self, user_id, service_key):
        """Worker only (separate admin secret): remove the sign-in account. Never called from a user request."""
        r = self.client.delete(f"{self.issuer}/admin/users/{user_id}",
                               headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"})
        if r.status_code not in (200, 204, 404):
            raise AuthError(f"could not delete the sign-in account ({r.status_code})")


def csrf_token(secret, session_token):
    """One token per session (HMAC of the session), so another site cannot guess it. Before sign-in the session is empty."""
    return hmac.new(secret.encode(), b"csrf:" + (session_token or "").encode(), hashlib.sha256).hexdigest()


def csrf_ok(secret, session_token, given):
    return hmac.compare_digest(csrf_token(secret, session_token), given or "")
