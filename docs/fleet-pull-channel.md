# The fleet pull channel — a node the controller cannot dial still works

**Status:** implemented on branch `pull-fleet` (Hub 0.6.193). Owner-approved direction 2026-10-20.

## The problem, measured

The Shops fleet spans three Tailscale accounts and one Shops controller (`hq-macmini`). For **the
three Macs below** the controller cannot dial back: reverse dialing failed for them, and their
outbound reachability was never proven (no controller build recorded a call from them). That is a
property of those three paths, not of Shops as a whole — five other Shops Macs answer the
controller's probe normally.

So `office-imac-blue-m1-8-256-hub`, `grace-2fs-imac-hub` and `kt-ksw-imac-blue-16-512-001-hub`
joined, and then never answered a single probe. `fleet_nodes.probe()` decided reachability **only**
by dialling the stored address, and `last_seen_at` was stamped only from a dial that answered.
Nothing ever answered, so nothing was ever stamped, so all three read `never_connected=true` for
ever — 51 hours after joining at the last measurement. Whether they were running was never proven:
the controller recorded no call from them, because the status call they make was read-only.

The address was frozen too: the roster address is written once, at join (`fleet_nodes.join`), and a
Mac never re-joins on its own.

**The channel that worked was the one the node starts.** A node's app already made an authenticated
call home every 60 s — the read-only `GET …/status` on builds before 0.6.193, the check-in after —
and its answer was never treated as evidence of life.

## The principle

One authenticated, node-originated channel carries everything the controller would otherwise push
or dial for. Dialling stays the first choice wherever it works; the pull channel is the fallback
when it does not. Every unit of work carries an **idempotent command id**, and the node **acks**
it, so a command delivered twice is executed once.

## Wire contract

### `POST /api/hub/fleet/nodes/{node_id}/checkin`

New. Accepted with the Fleet token **or** the node's own credential; a node credential may check in
only for its own id (checked in the route, as `GET …/status` already did). A controller older than
this build answers 404, and a node that gets 404 falls back to the existing `GET …/status`, so the
two sides can be updated in either order.

Request (every field optional; the node sends what it knows):

```json
{
  "node_id": "kt-ksw-imac-blue-16-512-001-hub",
  "address": "http://100.64.0.9:47873",     // this Mac's *current* address
  "name": "Shops iMac",
  "version": "0.6.193",
  "studios": [{"id": "image", "modality": "image"}],
  "workstation": { "enabled": true, "idle_only": true, "state": "idle" },
  "startup": { "open_at_login": true, "start_services_on_launch": true },
  "resources": { "host": {...}, "studios": {...}, "version": "0.6.193", ... },
  "seen": ["c_3f0a...", "c_91bb..."],
  "results": [ {"id": "c_3f0a...", "status": 200, "json": {"ok": true}} ]
}
```

`resources` is **this Mac's own answer to `GET /api/hub/resources?local_only=true`** — the exact
route the controller's probe dials — carried verbatim. One field gives the roster, the peer cache
and the scheduler the same facts a dial would have returned, so nothing is approximated.

Response:

```json
{
  "id": "kt-ksw-imac-blue-16-512-001-hub",
  "state": "member",              // "member" | "removed" | "unknown" (unchanged)
  "detail": null,
  "server_time": 1760000000.0,
  "reachable_via": "check-in",
  "commands": [
    { "id": "c_7d21...", "kind": "workstation", "method": "POST",
      "path": "/api/hub/workstation", "headers": {"Content-Type": "application/json"},
      "json": null, "content_b64": "eyJlbmFibGVkIjp0cnVlfQ==", "content_type": "application/json",
      "created_at": 1760000000.0, "expires_at": 1760000900.0 }
  ]
}
```

### The command envelope

`method` + `path` + (`content_b64`/`content_type`) is **exactly the HTTP request the controller
would have dialled**, against the node's own Hub. The node replays it on `127.0.0.1:<its own port>`
with **its own Hub token** and returns the status and body it got. The controller's Fleet token is
stripped from the command and never reaches the node.

That is the whole of the design: nothing has to know what a command *means*, and a pull command can
never drift from the dialled call it replaces, because it is that call.

A command is retired when the node acks it (`seen`/`results`) or when it expires
(`expires_at`, 900 s). An id the node has acked is never re-issued.

### Roster fields added

`last_checkin_at`, `checkin_address`, `checkin_version`, `checkin_workstation`,
`checkin_startup`, `checkin_current_job`, `checkin_last_stop`, `checkin_resources`,
`last_status_at`, and `last_seen_via` (`"probe"` | `"check-in"` | `"status"`). `last_seen_at` is
stamped by all three, and `never_connected` is cleared by any of them.

`"status"` is presence only: an authenticated `GET …/status` from a node's own credential proves
the Mac reached this controller, and nothing else — not a dial that works, not a version, not a Mac
that can be given pull work. It is kept in `last_status_at`, and it never overwrites a `check-in`
or `probe` reason: a build that polls status *and* checks in must keep reading `check-in` between
check-ins, or `dial_is_failing` and the pull transport would stop recognising a Mac they already
know they cannot dial. Only `"check-in"` makes a node check-in alive.

A node is **check-in alive** while `last_checkin_at` is inside `CHECKIN_FRESH_S` (180 s = three
missed ticks).

### Two answers for two kinds of request

* A **settings change** (Lite Mode, rename, startup, job priority) is answered `202` with
  `queued: true` and the command id. The Mac applies it on its next call home; nothing is waiting
  for a result. A caller reading `ok: true` as "done" would be reading a promise as a fact, so the
  status is 202 and the flag is set.
* **Work** (a test transcription, a picture analysis, a model install, a Trash listing) is
  something the caller expects an answer for, so the relay waits its own timeout for the Mac's
  answer and returns it — the same wait the dial would have cost.

### The rule that keeps "dial first" from meaning "wait ten minutes"

`probe` dials every node on every roster read and records the reason it last succeeded or failed as
`last_seen_via`. While that reads `check-in`, the controller's dial is known to be failing **now**,
so `dial_is_failing()` is true and a relay or the transport goes straight to the queue. The moment a
dial succeeds the reason goes back to `probe` and everything dials again — nothing is stuck behind a
stale belief.

## Dial path → pull replacement

| # | Dial path (controller → node) | Pull replacement |
|---|---|---|
| 1 | `fleet_nodes.probe()` — `GET /api/hub/resources?local_only=true` | The check-in's own `resources` field. A dial that fails, on a node that is check-in alive, reports `state: "reachable"`, `via: "check-in"`, with the node's facts. `last_seen_at` stamped, `never_connected` cleared. |
| 2 | `relay_startup` — `POST /api/hub/startup/request` | Queued command (202). |
| 3 | `relay_name` — `POST /api/hub/machine-label` | Queued command (202). The check-in's `name` also carries a rename up. |
| 4 | `relay_workstation` (**Lite Mode**) — `POST /api/hub/workstation` | Queued command (202). |
| 5 | `relay_work_policy` — `GET\|POST /api/hub/work-policy` | Queued command (202). |
| 6 | `relay_model` — `/api/hub/fleet/nodes/local/{model-packages/…/download, model-downloads/…, transcribe, vision/analyze, trash…, macos-trash-auto-empty/enable}` | Queued command, **waited** for the Mac's answer. |
| 7 | **Job dispatch** — `POST /studio/{modality}/api/generate/*`, `/studio/chat/v1/chat/completions`, `/studio/embeddings/v1/embeddings`, `/studio/vision/v1/vision/analyze`, `/studio/{…}/api/transcribe`, and their `GET`/`DELETE` job routes | `fleet_pull.PullTransport`, an `httpx.AsyncHTTPTransport` installed on the controller's shared outbound client (`monitor._client`). Every one of these already goes through that client, so **no broker, Chat or Subtitle call site changed**: the transport dials first and, on a transport error against a check-in-alive node, replays the same request through the queue. |
| 8 | **Job batches** — `POST\|GET\|DELETE /api/hub/jobs`, `/api/hub/transcription/jobs`, `/api/hub/voice-references` | Same transport. |
| 9 | **Trash** — `GET …/trash`, `POST …/trash/{move-to-macos-trash,delete}`, `POST …/macos-trash-auto-empty/enable` | Queued command, waited (they already route through `relay_model`). |
| 10 | **Peer poll** — `peers.refresh` → `GET /api/hub/resources?local_only=true` | `peers.one` takes the check-in snapshot directly when the dial is known to fail, so the poll sweep is not stalled. Without this the Mac would read online on the roster and still be invisible to the scheduler — a Mac that is up and never given work. |
| 11 | **Shared voices fleet-sync** — `PUT\|DELETE /studio/voice/api/voices/{id}/fleet-sync` | Same transport. |
| 12 | **Fleet-token rotation push** — `POST /api/hub/fleet` | Same transport. |
| 13 | **Model-memory release** — `POST /studio/{modality}/api/memory/release`, `GET\|PUT /studio/{modality}/api/memory-policy` | Same transport. |
| 14 | **Update offer / rollout** | **Already pull.** The node mirrors the controller's release (`release_sync.loop`, every 300 s) and its app decides from its own Hub. Nothing to add; the check-in now reports the Mac's version so the controller can see what it holds. |

## Rollout order

**Controller first, then nodes, then the Gateway. Both directions stay safe.**

1. **The Hub ships alone.** It gains the check-in route, the queue and the transport. Old nodes keep
   calling `GET …/status` — and the controller records that authenticated call as presence
   (`last_seen_via: "status"`), so an old node that can reach the controller stops reading
   `never_connected`. A node the controller *can* dial is unaffected: the transport dials first and
   only a transport failure reaches the queue.
2. **The node's app ships.** It POSTs the check-in. If the controller answers **404** (it is older),
   the app does nothing at all and the existing `GET …/status` call keeps reporting a removal. A Mac
   that never updates keeps working and keeps being shown honestly.
3. **The Gateway ships** (separate worktree): the flat machine row carries `node_via` so a reader
   can tell "we can dial it" from "it is dialling in", and `_node_post` recognises `202 + queued` as
   the controller having taken the change rather than as the Mac having applied it.

## What stays dial-only, and why

- **Nothing reachable.** Every controller→node request in `components/hub/*` and the native app is
  either an explicit relay (rows 2–6, 9) or goes through the shared outbound client (rows 7, 8,
  10–14).
- **`peers.py`'s dead relay helpers** (`control_remote`, `startup_services_status`,
  `install_remote_startup_service`, `retire_remote_startup_service`, `remove_remote_studio`,
  `install_remote_generation`, `start_remote_studio_update_repair`, `sync_owner_password`) build
  URLs against a node but have **no caller and no matching route in this build**, so there is
  nothing to give a pull twin to. They stay as they are.
- **`delivery.py`'s artifact receipt ack** and `transcription_jobs.py`'s ack create their own
  `httpx.AsyncClient` rather than using `monitor._client`, so the transport does not reach them.
  They are post-completion receipts on a job that has already run; a failure there is reported as a
  failed ack today, and a queued receipt would be answered after the caller had given up. Left on
  dial, deliberately.
- **The node's own removal** (`DELETE /api/hub/fleet/nodes/{id}`) already runs node → controller
  with its own on-disk retry (`pendingLeave`). Unchanged.

## Latency, stated plainly

A command reaches the Mac on its **next call home**, which is at most 60 s after it is queued. The
Mac then posts the answer back immediately rather than waiting for its next tick, so the round trip
is (≤60 s) + (the work). A settings change is answered `202` at once; work is waited for.
