# 1 Install Apps

Three apps that together turn an idea into a finished video. Install them once per Mac,
then point them at each other. This README explains what each one is for, how they link,
and the order to set them up.

| Folder | App | Version | What it does |
|---|---|---|---|
| `Story Studio/` | Story Studio KH | 1.4.367 | Writes and produces the episode: ideas, script, prompts, thumbnails, final video |
| `Gateway/` | GatewayKH | 0.2.205 | The middle man: one address, one model catalogue, routes work to your Macs |
| `Studio Hub/` | Studio Hub | its `focused-release-manifest.json` | Runs the models themselves — images, voice, subtitles, music |
| `KH Updater/` | KH Updater | its `khu-agent/VERSION` | Keeps the three apps up to date on every Mac, from the publisher's signed releases |

---

## The three roles

**Story Studio** is where you work. It turns an idea into a script, scene prompts, images,
narration, subtitles, a thumbnail and a finished video file, and it uploads to YouTube.
It also installs Content Packs you download from the Studio Library. Every model call goes
to GatewayKH, except optional duplicate screening, which runs EmbeddingGemma on the Mac itself.

**GatewayKH** is the coordinator. Story Studio talks to exactly one address — GatewayKH —
and GatewayKH decides which of your Macs should do the work. It holds the combined model
catalogue and a private shelf of your Content Packs. It does **not** generate anything, so
it needs no GPU and very little memory; it can run on a small always-on Mac.

> **The version numbers above are stamped on the drive, not taken from this file.**
> `packaging/kit_stamp_index.py` reads them from the newest `Story Studio/*.dmg`, the newest
> `Gateway/installers/*.dmg` and `KH Updater/khu-agent/VERSION` actually present and rewrites
> these rows — and the drive-root copy of this document — every time a kit is prepared.
> Hand-editing them here is only about giving the *repository* copy a sensible value; the drive
> always describes what it carries. Two rows name no version in this file for the same reason: the
> Studio Hub row points at the kit's generated `focused-release-manifest.json`, and the KH Updater
> row at the agent kit's own `VERSION`, so neither can describe a build the drive does not carry.

**Studio Hub** is the muscle. It runs the actual models on each Mac. One Mac is the
**controller** (it owns a site: itself plus every Mac that joins it) and the others are
**nodes**. GatewayKH talks only to controllers, never to nodes directly.

## How they link

```
Story Studio  ──(Base URL + API key)──►  GatewayKH  ──(Fleet token)──►  Studio Hub controller
                                                                              │
                                                                              ▼
                                                                       Studio Hub nodes
```

- **Story Studio → GatewayKH**: Settings → This Mac → **GenStudio KH**. Two fields: the
  Base URL (`http://<gateway-ip>:8787`) and the GenStudio API key. Every AI call goes
  here; Story Studio never talks to Studio Hub at all.
- **GatewayKH → Studio Hub**: the gateway's **Fleet** page. You add each controller by
  its address (`http://<tailscale-ip>:47873`) and that controller's **Fleet token**.
- **Node → controller**: the join is done on the **node's own** Studio Hub app, under
  Connections → "Join a controller", using the **controller's Fleet token**.

## The three credentials — which one goes where

This is the part that trips everyone up. There are three different secrets and they are
not interchangeable.

| Credential | Comes from | Goes to | What it is for |
|---|---|---|---|
| **Gateway API key** | GatewayKH → Settings (minted on first run) | Story Studio → GenStudio KH → API key | Lets Story Studio use the gateway |
| **Controller Fleet token** | Studio Hub → Connections → Copy Fleet token | GatewayKH → Fleet → Add a location | Lets the gateway send work to that controller |
| **Controller Fleet token** (again) | the same controller | the node's own Studio Hub → Join a controller | Lets a Mac join that controller |

The **Hub token** is never used for any of this. It is the Mac's own private key, it stays
on that Mac, and it is only for that Mac's owner to change settings. Nothing in this kit
contains a secret: every credential is added by hand on the machine that needs it.

## Setup order

Do it in this order. The linking steps each have a guide in `Gateway/`.

1. **Install the apps.** `Studio Hub/` and `Story Studio/` have their own README with
   the install steps. GatewayKH is in `Gateway/`.
2. **Install KH Updater on every Mac** — one double-click per Mac: `KH Updater/Install KH
   Updater.command`, with the publisher's enrolment window open at the Mac Studio first. It
   is what keeps the three apps current from then on, and it is worth doing before the Macs
   are spread around a site. `KH Updater/README.md` has the two-step version on the
   publisher. A Mac that already has the agent — every Hub Mac gets it from Studio Hub
   itself — says so and changes nothing.
3. **Gateway host** — install GatewayKH on one Mac and read its API key (guide
   `01-gateway-host.md`). Choose an always-on Mac if you want work to run overnight.
4. **Controller host** — install Studio Hub on a capable Mac, set it as **Controller**,
   tick **Tailscale** under Connections → Fleet, restart the services
   (`02-controller-host.md`).
5. **Join nodes** — on every other Mac, install Studio Hub and join the controller with
   its Fleet token (`03-join-nodes.md`).
6. **Add locations** — in GatewayKH, add each controller with its address and Fleet token
   (`04-add-locations.md`). Its models arrive disabled; enable the ones you want.
7. **Point Story Studio at the gateway** — Settings → This Mac → GenStudio KH: the Base
   URL and the gateway's API key.
8. **Verify** — `05-verify.md` proves the whole chain before you rely on it.
   `06-troubleshooting.md` is the measured failure list; `CHECKLIST.md` is the per-Mac
   tick list.

## Prerequisites

- Apple Silicon Macs, macOS 13 or newer.
- Every Mac on **one Tailscale tailnet**, with MagicDNS on.
- Mark the fleet Macs **non-expiring** in Tailscale, or their keys silently expire after
  about 180 days and the Mac drops off until someone visits it.
- The apps are ad-hoc signed, not notarised: the first launch needs right-click → Open
  (or the `xattr` line in each app's README).

## How one job travels

1. You write or generate a script in Story Studio and send it to production.
2. Story Studio asks GatewayKH for the models it needs (`/v1/models`) and submits the job.
3. GatewayKH picks a controller that has a suitable model and sends it the work.
4. The controller either runs it on its own Mac or hands it to one of its nodes,
   choosing by memory: audio and video go to the biggest Macs, images and subtitles to the
   smaller ones.
5. The artifact comes back through the controller to GatewayKH, which stores it and serves
   it to Story Studio.
6. Story Studio assembles the episode and, when you say so, uploads it.

## Which Mac should do what

- **Gateway host** — any Mac; small and always-on is ideal. It must be reachable by both
  Story Studio and every controller.
- **Controller** — a capable Mac. Also the machine you would keep on.
- **Nodes** — everything else. A Mac joins with its own app and its controller's Fleet token.
- Run **one** gateway. Story Studio points at a single URL; two gateways will fight.

## Folder map

```
1 Install Apps/
  README.md            ← you are here: the whole setup
  Gateway/             README.md + installers/ + the six setup guides
  Story Studio/        README.md + the installer DMG + Install Story Studio.command + EmbeddingGemma/
  Studio Hub/          README.md + the app, the models and its own guides
  KH Updater/          README.md + Install KH Updater.command + the agent kit + apps.json
```

Each folder's own `README.md` covers that app's role and install steps in detail. `KH Updater/`
is the one folder that is not part of the chain: it is the agent that keeps the three apps
current, and it belongs on every Mac.

## The one thing to remember

Work flows in one direction: **Story Studio → GatewayKH → controller → nodes**. If
something is not working, test it in that order — first does Story Studio reach the
gateway, then does the gateway reach the controller, then does the controller reach its
nodes. `Gateway/05-verify.md` walks exactly that path.
