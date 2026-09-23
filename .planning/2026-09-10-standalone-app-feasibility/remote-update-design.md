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

### Track A — components-only patch (the fast path, and the common case)

Most of our updates are Hub-side: Python under `Contents/Resources/components/`, plus
`hub_worker.py` and the worker modules. That set is **1.7 MB**, not 2.8 GB.

- The controller publishes a signed digest list of the changed files plus their contents.
- A node fetches the set, verifies every file against the digest, writes them into the bundle,
  and restarts only the Hub services.
- Limits, stated plainly: it edits inside an **ad-hoc signed** bundle, so the signature is
  invalidated (macOS tolerates this for ad-hoc, but it is not clean), and the release
  manifest's payload digest must be told that this is an expected, verified change rather
  than a corruption. If the native shell also changed — which it does whenever the UI moves —
  Track A cannot help and the node needs Track B.

### Track B — controller-served app update (the real answer)

```
publisher                controller                         node
  build -> DMG/manifest -> serves /api/hub/release/*  <----  GET manifest
                              |                             verify publisher hash
                              |                        <----  GET bundle (range/resume)
                              |                             verify, swap, restart
                              |                             report the new build back
```

1. **Publish.** A build is published to a controller as an *app release*: the bundle (or DMG),
   its SHA-256, the version and build string, and a publisher signature or, until the app is
   signed, a publisher-supplied hash kept on the controller.
2. **Advertise.** `GET /api/hub/release` on the controller returns what it holds. Additive;
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

## Trust, which is the whole difficulty

A node is being asked to install a bundle over the network. Without code signing there is no
identity to check, so the minimum is:

- the artifact's **SHA-256**, published with the release;
- the **publisher's** identity recorded on the controller (its own keyed hash of the release),
  so a node is not trusting an arbitrary middlebox;
- **https or a tailnet-only listener** for the transfer, which the tailnet already gives;
- an **explicit operator action** on the node. No silent installs.

This is the strongest argument for doing the app's code signing first: with a Developer ID the
node can verify the signature instead of trusting a hash the network handed it.

## Proposed additive surface (not built)

| Route | Purpose |
|---|---|
| `GET /api/hub/release` | what this controller holds: version, build, size, sha256, published_at, publisher |
| `GET /api/hub/release/artifact` | the bundle or DMG, range-capable |
| `POST /api/hub/release` | publish/replace what this controller holds (Hub token: it is the Mac owner's decision) |

Node side: an "Update available" banner in Connections with the advertised version, the size,
and one button; the existing restart notice for the services.

## Non-goals

- No silent or scheduled auto-update. An update interrupts work.
- No fleet-wide "update everything now" without each Mac's owner acting.
- No update of the **models** — those are already a separate copy step and are unchanged by an
  app release.
- No rollback of an update the node itself initiated and completed successfully.

## Effort, roughly

- Track A alone: small — a route, a verifier and a restart path. Half a day.
- Track B: the larger piece. Publishing plus advertising plus the node UI plus the verified
  swap and rollback: a day or two, most of it in the swap and the failure paths.
- Signing: independent, and it makes Track B's trust story much simpler.

## Open questions for the owner

1. Should the **controller** hold the release, or **GatewayKH** (which already holds the
   installers in its kit and is the one address Story Studio uses)? The controller is closer to
   the nodes; GatewayKH is where a build is published once for everything.
2. Do we want Track A at all, or is a 1-minute full swap acceptable now that Track B exists?
3. Sign the app before this, or ship the hash-only trust model first?

Recorded 2026-09-23. Nothing here is implemented; `main` carries the design only.
