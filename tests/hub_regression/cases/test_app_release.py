"""Controller-side remote app-update release routes.

Drives the real packaged app over both credentials so the routes, the Fleet
allowlist and the signature/hash gates are exercised together.  Every signing
key here is a throwaway generated per test; no real key is used.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat

import pytest

SCHEMA = "studiohub.app-release.v1"
# What GET /api/hub/release says about the delta update when no manifest is held.
NO_DELTA = {"schema": "studiohub.file-manifest.v1", "manifest": False}


@pytest.fixture
def hub(reset):
    from fastapi.testclient import TestClient
    from components.hub.standalone import create_app

    app = create_app(start_background=False)
    with TestClient(app) as client:
        yield client


def _hub_headers() -> dict:
    from components.hub.backend import auth
    return {"Authorization": "Bearer " + auth.load_token()}


def _fleet_headers() -> dict:
    from components.hub.backend import peers
    return {"Authorization": "Bearer " + peers.fleet_token()}


def _keypair() -> tuple[bytes, bytes]:
    from components.hub.backend import ed25519
    seed = secrets.token_bytes(32)
    return seed, ed25519.public_key(seed)


def _descriptor(artifact: bytes, seed: bytes, **overrides) -> dict:
    """A descriptor signed by ``seed``; overrides apply before signing."""
    from components.hub.backend import app_release, ed25519

    public = ed25519.public_key(seed)
    descriptor = {
        "schema": SCHEMA,
        "schema_version": 1,
        "version": "0.6.50",
        "build": "abc1234",
        "filename": "StudioHub-0.6.50-abc1234.dmg",
        "bytes": len(artifact),
        "sha256": hashlib.sha256(artifact).hexdigest(),
        "published_at": 1790000000,
        "publisher_id": "gatewaykh",
        "publisher_key_id": app_release.fingerprint(public),
    }
    descriptor.update(overrides)
    descriptor["signature"] = app_release.sign(descriptor, seed)
    return descriptor


def _body(descriptor: dict, artifact: bytes) -> dict:
    return {"descriptor": descriptor,
            "artifact_base64": base64.b64encode(artifact).decode("ascii")}


def _trust(client, seed: bytes, publisher_id: str = "gatewaykh") -> dict:
    from components.hub.backend import ed25519

    response = client.post(
        "/api/hub/release/trust",
        headers=_hub_headers(),
        json={"public_key": ed25519.public_key(seed).hex(),
              "publisher_id": publisher_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _nothing_stored() -> bool:
    from components.hub.backend import app_release
    return not (app_release.RELEASE_FILE.exists()
                or app_release.ARTIFACT_FILE.exists())


# ── the primitive the whole chain rests on ─────────────────────────────────


def test_ed25519_matches_rfc8032_test_vector():
    """Independent known-answer check; the routes below would pass even if the
    verifier and signer were wrong in the same way, this would not."""
    from components.hub.backend import ed25519

    seed = bytes.fromhex(
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    public = bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33"
        "bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")

    assert ed25519.public_key(seed) == public
    assert ed25519.sign(seed, b"") == signature
    assert ed25519.verify(public, b"", signature)
    assert not ed25519.verify(public, b"tampered", signature)
    # A non-canonical S (>= l) and a bit-flipped signature are both refused.
    order = 2**252 + 27742317777372353535851937790883648493
    non_canonical = signature[:32] + order.to_bytes(32, "little")
    assert not ed25519.verify(public, b"", non_canonical)
    flipped = bytearray(signature)
    flipped[40] ^= 0x01
    assert not ed25519.verify(public, b"", bytes(flipped))


# ── a good release ─────────────────────────────────────────────────────────


def test_good_release_is_accepted_stored_and_countersigned(hub):
    from components.hub.backend import app_release, ed25519

    gateway_seed, _ = _keypair()
    _trust(hub, gateway_seed)
    artifact = secrets.token_bytes(4096)
    descriptor = _descriptor(artifact, gateway_seed)

    response = hub.post("/api/hub/release", headers=_fleet_headers(),
                        json=_body(descriptor, artifact))
    assert response.status_code == 200, response.text
    stored = response.json()

    # The descriptor is kept exactly as received, plus the countersignature.
    for key, value in descriptor.items():
        assert stored[key] == value
    assert stored["controller_key_id"]
    assert stored["controller_signature"]

    identity = hub.get("/api/hub/release/identity",
                       headers=_fleet_headers()).json()
    assert stored["controller_key_id"] == identity["fingerprint"]
    controller_public = bytes.fromhex(identity["public_key"])
    assert app_release.verify_countersignature(
        stored, stored["controller_signature"], controller_public)
    # The gateway signature is by the gateway key the owner trusted, over the
    # descriptor as it was received (the controller fields were added after).
    assert app_release.verify(descriptor, stored["signature"],
                             ed25519.public_key(gateway_seed))

    # Both the descriptor and the artifact are on disk under the data dir.
    assert app_release.RELEASE_FILE.is_file()
    assert app_release.ARTIFACT_FILE.read_bytes() == artifact
    assert json.loads(app_release.RELEASE_FILE.read_text()) == stored

    # GET returns exactly what is held, plus the installed trust anchor.
    fetched = hub.get("/api/hub/release", headers=_fleet_headers())
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["release"] == stored
    assert fetched.json()["trusted_publisher"] == {
        "publisher_id": "gatewaykh",
        "fingerprint": hashlib.sha256(ed25519.public_key(gateway_seed)).hexdigest(),
        "public_key": ed25519.public_key(gateway_seed).hex(),
    }

    # The private key stays private, on disk 0600.
    mode = stat.S_IMODE(os.stat(app_release.CONTROLLER_KEY_FILE).st_mode)
    assert mode == 0o600
    assert "controller_private" not in response.text
    assert app_release.CONTROLLER_KEY_FILE.read_text().strip() not in response.text


def test_good_release_is_accepted_from_the_fleet_token(hub):
    """GatewayKH holds only the controller's Fleet token, so the push route must
    be reachable with it."""
    gateway_seed, _ = _keypair()
    _trust(hub, gateway_seed)
    artifact = b"fleet-pushed-artifact"
    descriptor = _descriptor(artifact, gateway_seed)

    response = hub.post("/api/hub/release", headers=_fleet_headers(),
                        json=_body(descriptor, artifact))
    assert response.status_code == 200, response.text
    assert hub.get("/api/hub/release",
                   headers=_fleet_headers()).json()["release"]["build"] == "abc1234"


# ── refusals ───────────────────────────────────────────────────────────────


def test_artifact_sha256_mismatch_is_refused_and_nothing_is_stored(hub):
    gateway_seed, _ = _keypair()
    _trust(hub, gateway_seed)
    good = b"a" * 64
    other = b"b" * 64  # same length, different bytes -> length passes, sha fails
    descriptor = _descriptor(good, gateway_seed)

    response = hub.post("/api/hub/release", headers=_hub_headers(),
                        json=_body(descriptor, other))
    assert response.status_code == 400, response.text
    assert "sha256" in response.json()["detail"].lower()
    assert _nothing_stored()
    assert hub.get("/api/hub/release",
                   headers=_fleet_headers()).json()["release"] is None


def test_release_signed_by_the_wrong_key_is_refused(hub):
    from components.hub.backend import app_release, ed25519

    trusted_seed, _ = _keypair()
    _trust(hub, trusted_seed)
    wrong_seed, _ = _keypair()
    artifact = b"artifact"
    descriptor = _descriptor(artifact, wrong_seed)  # names the wrong key id

    response = hub.post("/api/hub/release", headers=_fleet_headers(),
                        json=_body(descriptor, artifact))
    assert response.status_code == 400, response.text
    assert _nothing_stored()
    assert not app_release.verify(descriptor, descriptor["signature"],
                                 ed25519.public_key(trusted_seed))


def test_release_before_any_trusted_key_is_refused(hub):
    gateway_seed, _ = _keypair()
    artifact = b"artifact"
    descriptor = _descriptor(artifact, gateway_seed)  # no trust route call

    response = hub.post("/api/hub/release", headers=_hub_headers(),
                        json=_body(descriptor, artifact))
    assert response.status_code == 409, response.text
    assert "trust" in response.json()["detail"].lower()
    assert _nothing_stored()


@pytest.mark.parametrize("field,value", [("version", "9.9.9"), ("bytes", 1),
                                         ("sha256", "00" * 32)])
def test_tampered_descriptor_after_signing_is_refused(hub, field, value):
    gateway_seed, _ = _keypair()
    _trust(hub, gateway_seed)
    artifact = b"artifact-bytes"
    descriptor = _descriptor(artifact, gateway_seed)
    descriptor[field] = value  # signature now covers the old value

    response = hub.post("/api/hub/release", headers=_hub_headers(),
                        json=_body(descriptor, artifact))
    assert response.status_code == 400, response.text
    assert _nothing_stored()


def test_invalid_schema_is_refused(hub):
    gateway_seed, _ = _keypair()
    _trust(hub, gateway_seed)
    artifact = b"artifact"
    descriptor = _descriptor(artifact, gateway_seed, schema="something.else")

    response = hub.post("/api/hub/release", headers=_hub_headers(),
                        json=_body(descriptor, artifact))
    assert response.status_code == 400, response.text
    assert _nothing_stored()


def test_fleet_token_wrong_or_missing_signature_is_still_refused(hub):
    """The allowlist change must not weaken anything: the signature, not the
    token, authorises a release."""
    from components.hub.backend import app_release, ed25519

    trusted_seed, _ = _keypair()
    _trust(hub, trusted_seed)
    wrong_seed, _ = _keypair()
    artifact = b"artifact"

    # No signature at all.
    unsigned = _descriptor(artifact, trusted_seed)
    unsigned.pop("signature")
    response = hub.post("/api/hub/release", headers=_fleet_headers(),
                        json=_body(unsigned, artifact))
    assert response.status_code == 400, response.text

    # Claims the trusted key id, but signed by a key nobody trusts.
    impostor = _descriptor(
        artifact, wrong_seed,
        publisher_key_id=app_release.fingerprint(ed25519.public_key(trusted_seed)))
    response = hub.post("/api/hub/release", headers=_fleet_headers(),
                        json=_body(impostor, artifact))
    assert response.status_code == 400, response.text
    assert "signature" in response.json()["detail"].lower()
    assert _nothing_stored()


# ── artifact download ──────────────────────────────────────────────────────


def test_artifact_returns_exact_bytes_and_honours_range(hub):
    gateway_seed, _ = _keypair()
    _trust(hub, gateway_seed)
    artifact = bytes(range(256)) * 8  # 2048 distinct-ish bytes
    descriptor = _descriptor(artifact, gateway_seed)
    assert hub.post("/api/hub/release", headers=_hub_headers(),
                    json=_body(descriptor, artifact)).status_code == 200

    full = hub.get("/api/hub/release/artifact", headers=_fleet_headers())
    assert full.status_code == 200, full.text
    assert full.content == artifact
    assert full.headers["content-length"] == str(len(artifact))
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["x-content-sha256"] == descriptor["sha256"]

    partial = hub.get("/api/hub/release/artifact",
                      headers={**_fleet_headers(), "Range": "bytes=2-5"})
    assert partial.status_code == 206, partial.text
    assert partial.content == artifact[2:6]
    assert partial.headers["content-range"] == f"bytes 2-5/{len(artifact)}"
    assert partial.headers["content-length"] == "4"
    assert partial.headers["x-content-sha256"] == descriptor["sha256"]


def test_artifact_is_404_when_nothing_is_held(hub):
    response = hub.get("/api/hub/release/artifact", headers=_fleet_headers())
    assert response.status_code == 404, response.text


# ── tokens and allowlist ───────────────────────────────────────────────────


def test_trust_is_hub_only_and_get_routes_are_fleet_readable(hub):
    from components.hub.backend import auth

    seed, public = _keypair()
    empty = hub.get("/api/hub/release", headers=_fleet_headers())
    assert empty.status_code == 200
    assert empty.json() == {"release": None, "trusted_publisher": None, "delta": NO_DELTA}

    refused = hub.post("/api/hub/release/trust", headers=_fleet_headers(),
                       json={"public_key": public.hex(), "publisher_id": "gatewaykh"})
    assert refused.status_code == 403, refused.text

    accepted = hub.post("/api/hub/release/trust", headers=_hub_headers(),
                        json={"public_key": public.hex(), "publisher_id": "gatewaykh"})
    assert accepted.status_code == 200, accepted.text
    payload = accepted.json()
    assert payload["publisher_id"] == "gatewaykh"
    assert payload["fingerprint"] == hashlib.sha256(public).hexdigest()

    identity = hub.get("/api/hub/release/identity", headers=_fleet_headers())
    assert identity.status_code == 200, identity.text
    assert identity.json()["public_key"] and identity.json()["fingerprint"]


def test_release_routes_are_on_the_fleet_allowlist_but_trust_is_not():
    from components.hub.backend import auth

    assert auth.fleet_path_allowed("POST", "/api/hub/release")
    assert auth.fleet_path_allowed("GET", "/api/hub/release")
    assert auth.fleet_path_allowed("GET", "/api/hub/release/artifact")
    assert auth.fleet_path_allowed("GET", "/api/hub/release/identity")
    assert not auth.fleet_path_allowed("POST", "/api/hub/release/trust")


def test_trust_rejects_a_bad_key(hub):
    response = hub.post("/api/hub/release/trust", headers=_hub_headers(),
                        json={"public_key": "not-a-key", "publisher_id": "gatewaykh"})
    assert response.status_code == 400, response.text
    response = hub.post("/api/hub/release/trust", headers=_hub_headers(),
                        json={"public_key": "", "publisher_id": "gatewaykh"})
    assert response.status_code == 400, response.text


# ── what GET /api/hub/release reports about the installed key ──────────────


def test_get_release_reports_the_installed_trusted_publisher(hub):
    """The additive ``trusted_publisher`` field: null before an owner installs a
    gateway key, the exact fingerprint after, and never a fabricated value."""
    from components.hub.backend import ed25519

    seed, public = _keypair()
    fingerprint = hashlib.sha256(public).hexdigest()

    # Before any trust install the key is absent, reported as an explicit null.
    before = hub.get("/api/hub/release", headers=_fleet_headers())
    assert before.status_code == 200, before.text
    assert before.json() == {"release": None, "trusted_publisher": None, "delta": NO_DELTA}
    assert "trusted_publisher" in before.json()

    # After the owner installs one, GET reports it verbatim.
    _trust(hub, seed, publisher_id="gatewaykh")
    after = hub.get("/api/hub/release", headers=_fleet_headers())
    assert after.status_code == 200, after.text
    assert after.json() == {
        "release": None,
        "trusted_publisher": {"publisher_id": "gatewaykh",
                              "fingerprint": fingerprint,
                              # the key itself, so a node can pin it trust-on-first-use
                              "public_key": public.hex()},
        "delta": NO_DELTA,
    }

    # Setting a second key replaces the reported one; the old key is never kept.
    other_seed, other_public = _keypair()
    _trust(hub, other_seed, publisher_id="gatewaykh-two")
    replaced = hub.get("/api/hub/release", headers=_fleet_headers())
    assert replaced.json()["trusted_publisher"] == {
        "publisher_id": "gatewaykh-two",
        "fingerprint": hashlib.sha256(other_public).hexdigest(),
        "public_key": other_public.hex(),
    }

    # The "release" key still carries exactly the held descriptor (null here).
    assert replaced.json()["release"] is None


def test_get_release_release_key_is_unchanged_by_the_added_field(hub):
    """The ``release`` value a node reads must be byte-for-byte what it was."""
    gateway_seed, _ = _keypair()
    _trust(hub, gateway_seed)
    artifact = b"descriptor-is-not-touched"
    descriptor = _descriptor(artifact, gateway_seed)
    stored = hub.post("/api/hub/release", headers=_fleet_headers(),
                      json=_body(descriptor, artifact)).json()

    answer = hub.get("/api/hub/release", headers=_fleet_headers()).json()
    assert set(answer) == {"release", "trusted_publisher", "delta"}
    assert answer["release"] == stored
    # The descriptor itself gained nothing from the additive response field.
    assert set(answer["release"]) == set(stored)
    assert "trusted_publisher" not in answer["release"]


def test_trust_accepts_an_omitted_publisher_id(hub):
    """The publisher id is a display label; the owner's app may leave it blank."""
    seed, public = _keypair()
    response = hub.post("/api/hub/release/trust", headers=_hub_headers(),
                        json={"public_key": public.hex()})
    assert response.status_code == 200, response.text
    assert response.json()["fingerprint"] == hashlib.sha256(public).hexdigest()
    assert response.json()["publisher_id"] is None
    reported = hub.get("/api/hub/release", headers=_fleet_headers()).json()
    assert reported["trusted_publisher"] == {
        "publisher_id": None,
        "fingerprint": hashlib.sha256(public).hexdigest(),
        "public_key": public.hex(),
    }
