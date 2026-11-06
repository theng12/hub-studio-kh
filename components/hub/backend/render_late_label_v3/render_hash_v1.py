"""Render-only typed identity bytes. No DTO, signature or authorization validation.

Keep this stdlib-only module byte-identical in Gateway and Hub. Raw input must
enter through decode_render_json; hashing an already parsed object cannot detect
lost duplicate keys. Callers still validate their closed schema before hashing.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct

_KINDS = frozenset(("recipe", "manifest", "packet", "gateway-envelope", "hub-envelope"))
_UNSAFE_KEYS = frozenset(("__proto__", "prototype", "constructor"))
_MAX_BYTES = 1024 * 1024
_MAX_STRING_BYTES = 256 * 1024
_MAX_VISITS = 65536
_MAX_DEPTH = 32  # Root zero; object keys and values each consume a visit.
_MAX_INTEGER = 9007199254740991


def _invalid(reason):
    raise ValueError("Invalid render hash value: " + reason)


def _string_bytes(value):
    if len(value) > _MAX_STRING_BYTES:
        _invalid("string exceeds 256 KiB")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        _invalid("unpaired surrogate")
    if len(encoded) > _MAX_STRING_BYTES:
        _invalid("string exceeds 256 KiB")
    return encoded


def _key(key):
    if type(key) is not str or key in _UNSAFE_KEYS or any(ord(c) < 32 or ord(c) > 126 for c in key):
        _invalid("unsafe or non-ASCII object key")


def _encode(value, prefix):
    output = bytearray(prefix)
    active = set()
    visits = 0

    def emit(part):
        if len(output) + len(part) > _MAX_BYTES:
            _invalid("preimage exceeds 1 MiB")
        output.extend(part)

    def walk(item, depth):
        nonlocal visits
        if depth > _MAX_DEPTH:
            _invalid("depth exceeds 32")
        visits += 1
        if visits > _MAX_VISITS:
            _invalid("value/key visits exceed 65536")
        kind = type(item)
        if item is None:
            emit(b"n")
        elif kind is bool:
            emit(b"t" if item else b"f")
        elif kind is int or kind is float:
            # Check Python integers before conversion; never round a large int.
            if kind is int:
                if abs(item) > _MAX_INTEGER:
                    _invalid("unsafe integral magnitude")
            elif not math.isfinite(item) or (item.is_integer() and abs(item) > _MAX_INTEGER):
                _invalid("number must be finite with safe integral magnitude")
            emit(b"d" + struct.pack(">d", 0.0 if item == 0 else item))
        elif kind is str:
            text = _string_bytes(item)
            emit(b"s" + str(len(text)).encode("ascii") + b":")
            emit(text)
        elif kind is list or kind is dict:
            if id(item) in active:
                _invalid("cycle")
            if len(item) * (2 if kind is dict else 1) > _MAX_VISITS - visits:
                _invalid("value/key visits exceed 65536")
            active.add(id(item))
            emit((b"a" if kind is list else b"o") + str(len(item)).encode("ascii") + b":")
            if kind is list:
                for child in item:
                    walk(child, depth + 1)
            else:
                for key in item:
                    _key(key)
                for key in sorted(item):
                    walk(key, depth + 1)
                    walk(item[key], depth + 1)
            active.remove(id(item))
        else:
            _invalid("plain JSON data required")

    walk(value, 0)
    return bytes(output)


def canonical_render_value(value):
    """C(value). Scalar text is unchanged; field-specific restrictions are separate."""
    return _encode(value, b"")


def render_hash_preimage(kind, value):
    """The 1 MiB cap includes the entire domain prefix and C(value)."""
    if type(kind) is not str or kind not in _KINDS:
        _invalid("unsupported hash kind")
    return _encode(value, b"studio.render.hash.v1\0" + kind.encode("ascii") + b"\0")


def render_hash(kind, value):
    return hashlib.sha256(render_hash_preimage(kind, value)).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _key(key)
        if key in result:
            _invalid("duplicate decoded object key")
        result[key] = value
    return result


def _integer(token):
    # Bound conversion before Python constructs an arbitrary-sized integer.
    if len(token.lstrip("-")) > 16:
        _invalid("unsafe integral magnitude")
    value = int(token)
    if abs(value) > _MAX_INTEGER:
        _invalid("unsafe integral magnitude")
    return value


def _constant(token):
    _invalid("nonfinite JSON constant")


def _preflight(text):
    """Bound structure before allocating a decoded tree; not a JSON grammar.

    The stdlib string scanner handles escapes; json.loads remains the authority
    for grammar and _pairs for duplicates. The input is already at most 1 MiB.
    At most one bounded string is decoded here at a time.
    """
    depth = visits = index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in " \t\r\n,:":
            index += 1
            continue
        if char in "}]":
            depth -= 1
            if depth < 0:
                _invalid("invalid nesting")
            index += 1
            continue
        if depth > _MAX_DEPTH:
            _invalid("depth exceeds 32")
        visits += 1
        if visits > _MAX_VISITS:
            _invalid("value/key visits exceed 65536")
        if char in "{[":
            depth += 1
            index += 1
        elif char == '"':
            value, index = json.decoder.scanstring(text, index + 1, True)
            _string_bytes(value)
        else:
            # Skip one primitive candidate; the actual decoder validates it.
            index += 1
            while index < length and text[index] not in ' \t\r\n,:{}[]"':
                index += 1


def decode_render_json(raw):
    """Strict bounded UTF-8 JSON decode. Does not establish any render DTO schema.

    Accept bytes only, refuse a leading UTF-8 BOM, duplicate decoded keys,
    nonfinite/unsafe numbers, invalid scalars and bounded-resource violations.
    """
    if type(raw) is not bytes or len(raw) > _MAX_BYTES:
        _invalid("raw JSON must be bytes of at most 1 MiB")
    if raw.startswith(b"\xef\xbb\xbf"):
        _invalid("UTF-8 BOM is forbidden")
    try:
        text = raw.decode("utf-8", errors="strict")
        _preflight(text)
        value = json.loads(text, object_pairs_hook=_pairs, parse_int=_integer, parse_constant=_constant)
        canonical_render_value(value)
        return value
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid render JSON encoding or nesting") from exc
