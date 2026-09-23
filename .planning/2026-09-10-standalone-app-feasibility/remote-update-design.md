# Remote update for Studio Hub — design, not yet built

Status: **designed, deliberately not implemented (owner decision 2026-09-23)**. The owner
continues to update Macs from an SSD. This file exists so the work is ready and the
constraints are already understood when it is wanted.

## What is true today (measured)

| Thing | Size | Replaced by an update |
|---|---|---|
| `/Applications/Studio Hub.app` | 2.8 GB | **Yes** — swapped wholesale |
| `~/StudioHubModels` | 25 GB | No — outside the bundle |
| `~/Library/Application Support/StudioHub Standalone/` | 1.3 GB | No — outside |
| The Hub's Python inside the bundle | 1.7 MB | (the only part most updates change) |

An update today is `1 - Install the app.command` from an SSD, or the DMG: a **full bundle
replacement** that leaves models, settings, voices, jobs and the database untouched. There is
no delta mechanism, no update feed and no auto-update (unlike Story Studio, which is Electron
and has `latest-mac.yml`).

The Hub exposes **no update or maintenance route**. The controller↔node link carries work,
not software. The only remote install/repair code (`peers.install_remote_generation`,
`peers.start_remote_studio_update_repair`) is legacy Pinokio-era and is not wired into the
standalone build. So today an update is a physical visit to each Mac.

## Goal

One place to publish a build; every Mac already in the fleet picks it up on its own, verifies
it, installs it and restarts — without a drive visit. New Macs still bootstrap from an SSD.

## Two tracks, because the changes are not the same size

## Decisions (owner, 2026-09-23)

1. **GatewayKH holds the release** — it already holds the installers in its kit and is the one
   address Story Studio uses, so a build is published once for the whole estate.
2. **Track A is not wanted.** A full swap it is; see below for why, kept here so the reasoning
   is not lost.
3. **The node pins the publisher at pairing** — no Apple Developer ID. An update must be signed
   by the key the node already trusts, so the network cannot hand it arbitrary code.

### The consequence of (1) worth stating plainly

Nodes never talk to GatewayKH: the gateway talks to **controllers only**, and a node's only peer
is its controller. So "GatewayKH holds the release" makes it the **source of truth**, not the
thing every node fetches from. The chain is therefore:

```
build -> GatewayKH (publishes)  ->  controller (mirrors)  ->  node (pulls from its controller)
```

A controller caches the artifact once from GatewayKH and serves it to its own nodes, which keeps
the existing topology intact and keeps the trust anchor where it already is: the node pinned its
**controller's** key when it joined, so the controller is who it can verify.

The alternative — giving every node the gateway's address for updates — would be a new outbound
path and a second trust relationship per node, for no gain.

## Track A — considered and NOT chosen

Most of our recent releases change the native shell as well as the Hub, so a components-only
patch would rarely apply; it edits *inside* an ad-hoc-signed bundle, invalidating the signature;
and it breaks the one property Track B gives us, that every Mac is running a byte-identical,
**verified** artifact. A one-minute full swap that keeps every Mac provably identical is worth
more than saving the minute. Revisit only if many Hub-only fixes start shipping.

### Track B — the app update (the chosen path)

```
publisher                controller                         node
  build -> DMG/manifest -> mirrors from GatewayKH     <----  GET manifest
                              |                             verify against the pinned key
                              |                        <----  GET bundle (range/resume)
                              |                             verify, swap, restart
                              |                             report the new build back
```

1. **Publish.** A build is published to GatewayKH as an *app release*: the bundle (or DMG), its
   SHA-256, the version and build string, and its signature by the publisher key.
2. **Mirror.** A controller fetches the release once and holds it, so its nodes do not need to
   reach the gateway. `GET /api/hub/release` on the controller returns what it holds. Additive;
   Fleet-token reachable, so every node can read it.
3. **Decide.** A node compares its own `StudioHubBuild` with the advertised one. The node's
   owner chooses when to take it — never silent: an update restarts the Hub and drops any
   running work, so it is an operator decision, offered as a banner.
4. **Fetch.** `GET /api/hub/release/artifact` with range support, so a 1.1 GB DMG survives a
   dropped tailnet connection.
5. **Verify.** SHA-256 over the artifact, then the per-file digests over the unpacked bundle.
   Refuse on any mismatch. Record the publisher identity with the release.
6. **Swap.** The same careful path `packaging/install_local.sh` now uses: stage beside the
   target, verify the runtime inside it, move the old aside, move the new in, verify the build
   that landed, and only then remove the old. Never a partial bundle in place.
7. **Restart and report.** Restart the services, then report the new build to the controller
   so the Fleet page can show which Macs are on which build.
8. **Roll back** by keeping the previous bundle until the new one has started and answered its
   health check. If it does not, put the old one back and say so.

## Trust — the publisher key is pinned at pairing

A node is asked to install a bundle over the network, so it must be able to tell an update from
an impostor. The decision is to **pin the publisher's key on the node when it pairs**:

- At join, the node records the controller's publisher key (fingerprint + public key) with the
  membership it already stores. That is the trust anchor, and it is established once, out of
  band, the same way the Fleet token is.
- Every release carries a signature by that key. A node verifies the signature **before** it
  looks at the hash, and refuses a release whose key it does not already hold.
- A rotating or replaced key is an explicit act on the node, not something the network can do:
  a new key must be accepted by the Mac's owner, which is the point.
- The SHA-256 stays as a corruption check, not as the trust decision.
- The transfer is tailnet-only, and the install is an **explicit operator action**. No silent
  installs.

No Apple Developer ID is involved, so this does not change distribution and does not remove the
first-launch right-click. It does close the hole that hash-only trust leaves open: a compromised
controller can no longer push arbitrary code to its nodes.

## Proposed additive surface (not built)

GatewayKH publishes; the controller mirrors and serves; the node pulls.

| Side | Route | Purpose |
|---|---|---|
| GatewayKH | the release publish + advertise routes | source of truth, published once |
| Controller | `GET /api/hub/release` | what this controller has mirrored: version, build, size, sha256, publisher key id, signature, published_at |
| Controller | `GET /api/hub/release/artifact` | the bundle or DMG, range-capable |
| Controller | `POST /api/hub/release` | mirror/replace what this controller holds (Hub token: it is the Mac owner's decision) |

Node side: an "Update available" banner in Connections showing the advertised version, its size
and who signed it, with one button; the existing restart notice applies the services restart.
A node that does not already hold the publisher's key says so instead of offering the update.

## Non-goals

- No silent or scheduled auto-update. An update interrupts work.
- No fleet-wide "update everything now" without each Mac's owner acting.
- No update of the **models** — those are already a separate copy step and are unchanged by an
  app release.
- No rollback of an update the node itself initiated and completed successfully.
- No auto-accept of a new publisher key. That is an owner decision on the node.

## Effort, roughly

- The controller's mirror routes and the node's fetch-and-verify path: the bulk of it.
- The verified swap and rollback already exist in spirit — `packaging/install_local.sh` does
  exactly this dance locally, and the node update should reuse it.
- The publisher key pinning is small; the discipline is in refusing everything else.

## Status

Decided 2026-09-23 (the three questions above are answered at the top of this file). Nothing
here is implemented; `main` carries the design only. The owner continues to update Macs from an
SSD, which stays the bootstrap path for a new Mac either way.

## Wire contract (chosen, to build against)

**Push, not pull.** GatewayKH already reaches controllers and holds their Fleet tokens; a
controller reaching GatewayKH would be a new outbound path and a second credential on every
Hub. So GatewayKH **pushes** a release to each controller.

**Two signatures, one chain.** The node's trust anchor is its controller, because that is what
it pinned at join.

```
GatewayKH signs the descriptor          controller verifies it, then countersigns
        |                                        |
        +--> POST /api/hub/release -------------+--> node verifies the CONTROLLER's
             (artifact + descriptor)                  countersignature with the key it
                                                      pinned, and the sha256 inside it
```

**Release descriptor** — one JSON object, canonical form is sorted keys, no whitespace:

```json
{
  "schema": "studiohub.app-release.v1",
  "schema_version": 1,
  "version": "0.6.50",
  "build": "abc1234",
  "filename": "StudioHub-0.6.50-abc1234.dmg",
  "bytes": 1234567,
  "sha256": "<64 hex over the artifact bytes>",
  "published_at": 1790000000,
  "publisher_id": "gatewaykh",
  "publisher_key_id": "<hex fingerprint of the signing key>",
  "signature": "<base64 ed25519 over the canonical descriptor WITHOUT this field>"
}
```

The `sha256` is inside the signed bytes, so the signature binds the artifact: verifying the
signature and then the hash is enough, and either failing refuses the release.

**Controller routes (Studio Hub Hub):**

| Route | Token | Purpose |
|---|---|---|
| `POST /api/hub/release` | Hub | accept a pushed release: `{"descriptor": {...}, "artifact_base64": ...}` or multipart; verify the gateway signature, verify the sha256, store both, countersign |
| `GET /api/hub/release` | Fleet | what this controller holds: the descriptor with the controller's countersignature, or `{"release": null}` |
| `GET /api/hub/release/artifact` | Fleet | the artifact, range-capable, `X-Content-SHA256` |
| `POST /api/hub/release/trust` | Hub | set or replace the trusted gateway publisher key — an owner action on this Mac, never implied |

**Node side:** reads `GET /api/hub/release` through its controller, compares `build` with its own
`StudioHubBuild`, and offers the update; on accept it verifies the countersignature against the
key pinned at join, verifies the sha256, swaps with the same staged path as
`packaging/install_local.sh`, restarts, and reports the new build back.

**Trust bootstrap:** the controller's own publisher key is generated on first use and shown with
its fingerprint, so a joining node can pin it; the gateway's key is set on the controller by the
owner (`/api/hub/release/trust`) and is what makes a pushed release acceptable.
