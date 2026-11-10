"""Authenticator-app codes (TOTP, RFC 6238: 6 digits, 30-second steps, SHA-1, as used by
Google Authenticator, Microsoft Authenticator, 1Password…) and one-time recovery codes."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from django.contrib.auth.hashers import check_password, make_password

STEP, DIGITS, WINDOW = 30, 6, 1          # accept the previous / next 30 s too (phone clocks drift)


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    n = struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF
    return str(n % 10 ** DIGITS).zfill(DIGITS)


def verify(secret, code, last_step=0, now=None):
    """The time step the code belongs to (to store, so it can't be reused), or None."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    current = int((now or time.time()) // STEP)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if step > last_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def otpauth_uri(secret, account, issuer):
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"


def qr_svg(uri):
    import io

    import segno
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, dark="#111", light="#fff", xmldecl=False)
    return buf.getvalue().decode()


def new_recovery_codes(n=10):
    """(codes to show once, hashes to store)."""
    codes = ["-".join(secrets.token_hex(2) for _ in range(2)) + "-" + secrets.token_hex(2) for _ in range(n)]
    return codes, [make_password(c) for c in codes]


def use_recovery_code(tf, code):
    code = (code or "").strip().lower().replace(" ", "")
    if len(code) < 10:
        return False
    for i, h in enumerate(tf.recovery_codes):
        if check_password(code, h):
            tf.recovery_codes = tf.recovery_codes[:i] + tf.recovery_codes[i + 1:]
            tf.save(update_fields=["recovery_codes"])
            return True
    return False
