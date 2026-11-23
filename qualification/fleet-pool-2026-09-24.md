# Fleet pool visibility — a node and its controller can both see the group

**Date:** 2026-09-24 · **Version:** 0.6.57 · **Scope:** Hub (`components/hub/`), native shell
(`native/StudioAPI.swift`, `native/StudioViews.swift`)

**Owner report:** *"after connecting each of the nodes, it doesn't show its own pool. And also in
the controller, I don't see the connected pool of nodes too. We need to have and show that."*

Two separate faults, one shared cause: **the pool was never a fact either end held.** A controller
listed whichever Macs happened to be in its dispatch registry, and a node held a borrowed
credential with no record of whose it was, so it could not name its own group at all.

## What was measured, and how

`tests/test_fleet_pool_visibility.py` starts two real Hub processes on loopback ports and drives
the documented join: the node adopts the controller's Fleet Token **with the controller's
address**, records its role, and posts itself to the controller's roster. Every assertion below is
against a live Hub over a socket, not a fixture.

| Observation | Before | After |
| --- | --- | --- |
| Controller: joined node in `/api/hub/fleet/overview` | present but `reachable=false`, *"this Mac has not reported in yet"* for ~4 s after the join, until the 12 s peer poll caught up | `reachable=true` immediately — the roster is probed directly |
| Controller: node switched off | row disappears from the pool | row stays with `reachable=false` and a reason |
| Controller: `summary.total` | counted registry machines only | counts the whole roster |
| Node: `/api/hub/fleet/overview` | `{'local'}` — this Mac only | `local` + the controller + every sibling node |
| Node: which controller it joined | **not recorded anywhere in the Hub** | `parent_controller_url`, set at adoption |
| Node: "Joined nodes" section | *"No Mac has joined this one yet"* | names the group's controller and how to leave it |

Reproduced live before the fix (controller row at `t=0s`: `reachable=False`,
`detail='this Mac has not reported in yet'`; `t=4.0s`: `reachable=True`) and re-run after.

## The four changes

1. **Adoption records the controller** (`POST /api/hub/fleet/adopt` takes an optional
   `controller_url`, validated by `fleet_nodes.validate_address`). This is the root fix: joining a
   controller *is* adopting its credential, so the moment the credential changes is the moment the
   group changes. The address previously lived only in the app's preferences file
   (`configuration.controllerAddress`), so a reinstall, or a second app window, left the Hub
   holding a borrowed token with no idea whose.

   An adoption that does not name a controller **clears** the recorded one rather than keeping the
   previous value. A stale address shows the wrong Mac as the head of the group; an absent one is
   recoverable by re-joining and is visibly unknown.

2. **`parent_controller_url` no longer dies on an unrelated save** (`control_plane.save_settings`).
   It was cleared whenever the role was not `"agent"`, so a Mac that had joined but had not yet
   been switched to Node lost the record on the next rename or listen-address change. It is now
   tied to the credential: present exactly as long as `fleet_credential_origin == "adopted"`.
   Promotion to controller still mints a fresh token and clears it, which is the containment the
   five-controller split depends on.

3. **`fleet_nodes.pool()`** answers the whole group from whichever end the Mac is on. A controller
   reads its own roster; a node reads the roster back from the controller it joined
   (`GET <controller>/api/hub/fleet/nodes` with the adopted token). Both ends then return the same
   list. `inventory()` gained a `machine` field so a node can name the head of its group.

   Best-effort: an unreachable controller answers `readable: false` and the controller is still
   listed as a machine that is not answering — the difference between a Mac whose group is down
   and a Mac that was never in one.

4. **`/api/hub/fleet/overview` is built from the group, not the dispatch registry.** Roster rows
   are probed live, so a machine that is off reads as off rather than as absent, and a member this
   Mac runs no studio for still appears. Rows gained `role` and `address`; the response gained
   `role`, `controller` and `roster_readable`.

**UI:** the pool is the existing Fleet overview, now populated on both ends. Rows carry a
Controller/Node badge and their address; the header says which group is being shown and, when the
controller is not answering, that the list is incomplete. On a node the "Joined nodes" section
names the group instead of claiming the Mac is alone, and the "Send here" pin is not offered —
a node does not choose where work runs.

## Evidence

- **11/11** `tests/test_fleet_pool_visibility.py` (two live Hubs; includes two-node pools, an
  offline node, an offline controller, and the unnamed-adoption case).
- **126 passed** across the fleet, roster, credential-origin, staging, settings and Hub suites.
- **Native suites pass** — `native/build.sh`: boundary, API contract, lifecycle. New assertions
  cover the address sent at adoption and the decoding of `role` / `controller` / `roster_readable`.
- **App-level, from both ends** — `./tests/native/pool-probe.sh` runs the app's own
  `NativeWorkspaceStore` against two joined Hubs:

```
===== what the app shows on the CONTROLLER =====
role: controller | origin: minted
overview role: controller | member: false | roster readable: true
  row: local          | This Mac        | controller | reachable: true | -
  row: studio-mini-02 | Studio Mini 02  | agent      | reachable: true | http://127.0.0.1:62104
own roster: controller, 1 node(s)

===== what the app shows on the NODE =====
role: agent | origin: adopted
overview role: agent | member: true | roster readable: true
controller: controller-mac | reachable | http://127.0.0.1:62102
  row: local       | This Mac            | agent      | reachable: true | -
  row: control-mac | controller-mac | controller | reachable: true | http://127.0.0.1:62102
own roster: agent, 0 node(s)
```

Screenshots are not used as evidence here: `native/render-page.sh` renders the page as
disconnected unless the operator's own services are running (`connected` reads
`studio.readyURL`), so a render of this screen cannot show a live pool. The store-level read
above is the same view model the page binds to, and it is reproducible.

## Known limits

- **Two probes per Fleet page visit.** `/api/hub/fleet/nodes` and `/api/hub/fleet/overview` each
  probe every node, so opening Connections on a 50-Mac controller issues ~100 requests. Probes run
  concurrently and are bounded by the 6 s timeout, so the page is not slower than before by more
  than the parallel round trip — but at the 64-node cap this is worth collapsing onto one shared
  read. Not fixed here: it is a load characteristic, not a correctness one.
- **A node does not list its own registration id.** It shows itself as "This Mac" under `local`,
  which is how the controller shows itself too, so the two ends are consistent; the roster id is
  deliberately not repeated as a second row.
- Not exercised on real hardware in this change: everything above is loopback Hub-to-Hub on one
  machine (the 32 GB M2 Max). Multi-Mac behaviour over a tailnet is the same code path — the peer
  path already runs in production between studios — but it has not been re-measured across two
  physical Macs since this change.

## Release state

`0.6.57 / 12e377f` — staged to `.build/StudioHub-0657/`, installed to `/Applications`, DMG
`dist/StudioHub-0.6.57-12e377f.dmg` (mounted and read back as 0.6.57 with all six components), and
all four `KH-STUDIO-*` kits refreshed to 0.6.57 with `verify-kit.sh` reporting `RESULT: PASS` on
each. Not pushed.
