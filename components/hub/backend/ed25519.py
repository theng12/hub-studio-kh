"""Ed25519 (RFC 8032) in pure Python, for the remote-update trust chain.

Why this file exists
--------------------
The bundled Studio Hub runtime (``.build/voice-runtime``) does **not** ship
``cryptography`` or ``pynacl``, and the release feature must not grow a new
binary dependency.  This module implements exactly the two operations the
release contract needs - key derivation/signing and verification - using only
``hashlib`` from the standard library.  The field arithmetic is the standard
twisted-Edwards group over ``2**255 - 19``; point operations use extended
coordinates so a verify is a few milliseconds rather than seconds.

Public interface (raw bytes, no encoding):
    SEED_SIZE, PUBLIC_KEY_SIZE, SIGNATURE_SIZE
    public_key(seed) -> 32 raw bytes
    sign(seed, message) -> 64 raw bytes
    verify(public_key, message, signature) -> bool

The encodings used on the wire (hex/base64) are chosen and documented in
``app_release.py``; this module never encodes anything.
"""

from __future__ import annotations

import hashlib

# Curve constants (RFC 8032 section 5.1).
q = 2**255 - 19
l = 2**252 + 27742317777372353535851937790883648493
d = (-121665 * pow(121666, q - 2, q)) % q
_I = pow(2, (q - 1) // 4, q)

SEED_SIZE = 32
PUBLIC_KEY_SIZE = 32
SIGNATURE_SIZE = 64

# Base point B = (x, y) lifted to extended coordinates (X, Y, Z, T).
_By = (4 * pow(5, q - 2, q)) % q
_Bx = None


def _recover_x(y: int, sign: int) -> int | None:
    """Recover the x coordinate for y, or None when (x, y) is not on the curve."""
    u = (y * y - 1) % q
    v = (d * y * y + 1) % q
    # Candidate for sqrt(u/v): u * v**3 * (u * v**7)**((q-5)/8), which avoids a
    # separate modular inverse and matches RFC 8032's decompression.
    v3 = pow(v, 3, q)
    v7 = pow(v, 7, q)
    uv3 = (u * v3) % q
    uv7 = (u * v7) % q
    x = (uv3 * pow(uv7, (q - 5) // 8, q)) % q
    if (v * x * x - u) % q != 0:
        x = (x * _I) % q
    if (v * x * x - u) % q != 0:
        return None
    if x == 0 and sign:
        return None
    if (x & 1) != sign:
        x = q - x
    return x


_Bx = _recover_x(_By, 0)
assert _Bx is not None
_B = (_Bx, _By, 1, (_Bx * _By) % q)
_IDENTITY = (0, 1, 1, 0)


def _point_add(p, r):
    """add-2008-hwcd-3, the complete formula for a = -1 twisted Edwards.

    Extended coordinates: x = X/Z, y = Y/Z, T = X*Y/Z.
    """
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = r
    a = ((y1 - x1) * (y2 - x2)) % q
    b = ((y1 + x1) * (y2 + x2)) % q
    c = (2 * t1 * t2 * d) % q
    dd = (2 * z1 * z2) % q
    e = (b - a) % q
    f = (dd - c) % q
    g = (dd + c) % q
    h = (b + a) % q
    return ((e * f) % q, (g * h) % q, (f * g) % q, (e * h) % q)


def _point_double(p):
    """dbl-2008-hwcd, doubling for a = -1 twisted Edwards."""
    x1, y1, z1, _ = p
    a = (x1 * x1) % q
    b = (y1 * y1) % q
    c = (2 * z1 * z1) % q
    # D = a*A = -A; G = D + B; H = D - B; E = (X+Y)^2 - A - B.
    g = (b - a) % q
    h = (-a - b) % q
    e = ((x1 + y1) * (x1 + y1) - a - b) % q
    f = (g - c) % q
    return ((e * f) % q, (g * h) % q, (f * g) % q, (e * h) % q)


def _scalar_mult(scalar: int, point) -> tuple[int, int, int, int]:
    result = _IDENTITY
    addend = point
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_double(addend)
        scalar >>= 1
    return result


def _encode_point(point) -> bytes:
    x, y, z, _ = point
    z_inv = pow(z, q - 2, q)
    x = (x * z_inv) % q
    y = (y * z_inv) % q
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode_point(encoded: bytes):
    """Decode one compressed point, rejecting non-canonical y and bad points."""
    if len(encoded) != PUBLIC_KEY_SIZE:
        return None
    raw = int.from_bytes(encoded, "little")
    sign = (raw >> 255) & 1
    y = raw & ((1 << 255) - 1)
    if y >= q:
        return None
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, (x * y) % q)


def _clamped_scalar(seed: bytes) -> int:
    digest = hashlib.sha512(seed).digest()
    a = bytearray(digest[:32])
    a[0] &= 248
    a[31] &= 127
    a[31] |= 64
    return int.from_bytes(bytes(a), "little")


def public_key(seed: bytes) -> bytes:
    """Derive the 32-byte public key from a 32-byte private seed."""
    if len(seed) != SEED_SIZE:
        raise ValueError("an Ed25519 private seed is exactly 32 bytes")
    return _encode_point(_scalar_mult(_clamped_scalar(seed), _B))


def sign(seed: bytes, message: bytes) -> bytes:
    """Produce the 64-byte deterministic Ed25519 signature (R || S)."""
    if len(seed) != SEED_SIZE:
        raise ValueError("an Ed25519 private seed is exactly 32 bytes")
    digest = hashlib.sha512(seed).digest()
    a = _clamped_scalar(seed)
    public = _encode_point(_scalar_mult(a, _B))
    prefix = digest[32:]
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % l
    r_encoded = _encode_point(_scalar_mult(r, _B))
    k = int.from_bytes(hashlib.sha512(r_encoded + public + message).digest(), "little") % l
    s = (r + k * a) % l
    return r_encoded + s.to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Strict RFC 8032 verification.

    Rejects a malformed/non-canonical public key or R, and a non-canonical S
    (S must be < l).  The group equation checked is ``S*B == R + k*A``.
    """
    if len(public) != PUBLIC_KEY_SIZE or len(signature) != SIGNATURE_SIZE:
        return False
    point_a = _decode_point(public)
    if point_a is None:
        return False
    r_encoded = signature[:32]
    s = int.from_bytes(signature[32:], "little")
    if s >= l:
        return False
    point_r = _decode_point(r_encoded)
    if point_r is None:
        return False
    k = int.from_bytes(hashlib.sha512(r_encoded + public + message).digest(), "little") % l
    left = _scalar_mult(s, _B)
    right = _point_add(point_r, _scalar_mult(k, point_a))
    return _encode_point(left) == _encode_point(right)
