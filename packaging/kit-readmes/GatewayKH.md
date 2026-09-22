# GatewayKH — the coordinator

**Version 0.2.1.** Installer: `installers/gateway-kh-0.2.1-722e1f3-202609221240-arm64.dmg`

## What it is for

GatewayKH sits between Story Studio and your Studio Hub Macs. Story Studio talks to
exactly one address — this app — and GatewayKH decides which Mac does the work.

It:

- presents **one** address and **one** model catalogue to Story Studio, merging every
  controller's models with about 17 cloud providers;
- routes each job to a controller that has a suitable model;
- receives the finished artifacts, stores them, and serves them back to Story Studio;
- serves your private Content Pack shelf to Story Studio's Content Store.

It does **not** generate anything, so it needs no GPU and very little memory. It can run
on a small always-on Mac. It never talks to a Studio Hub **node** — only to controllers —
and it cannot join or unjoin a Mac.

## Install

1. Open `installers/gateway-kh-0.2.1-722e1f3-202609221240-arm64.dmg`.
2. Drag **gateway-kh** into **Applications**.
3. It is ad-hoc signed, so the first launch needs **right-click → Open**, or:
   `xattr -dr com.apple.quarantine /Applications/gateway-kh.app`
4. Open it. It starts its own gateway and puts an item in the menu bar, with a normal
   window in the Dock.
5. **First run mints its own API key.** Open Settings and copy it — Story Studio needs it.

## Settings worth knowing

- **API key** — minted on first run; this is what Story Studio uses.
- **Base URL / Tailscale address / port** — the port is **8787** in this build.
- **Start at login** and **Keep running**.
- **Data folder** — `~/Library/Application Support/gateway-kh/`, containing
  `data/gateway.sqlite3`, `data/credentials.json` (0600), `data/fleet.json`,
  `data/artifacts/`, `data/voices.json`.

If Story Studio runs on another Mac, bind the gateway beyond loopback so that Mac can
reach it, and give Story Studio this Mac's Tailscale address.

## How it links

**To Studio Hub controllers** — you add each one on the **Fleet** page:

- **Name**, **Address** `http://<tailscale-ip>:47873`, and that controller's **Fleet
  token** (copied from the controller's Connections page — *not* its Hub token).
- Its models arrive **disabled**; enable the ones you want on the **Models** page.

Behind that it calls the controller's Hub API: `GET /api/hub/model-packages` to discover
models, `POST /api/hub/jobs` to send work, `GET /api/hub/stats`, and
`POST /api/hub/jobs/{batch}/items/{i}/ack` to confirm a collected result.

**To Story Studio** — Story Studio is pointed at this app's base URL (`/v1/*`), with the
API key as a bearer token. That is the whole of Story Studio's configuration.

## Guides in this folder

| File | Use |
|---|---|
| `01-gateway-host.md` | install and configure GatewayKH |
| `02-controller-host.md` | install and configure a Studio Hub controller |
| `03-join-nodes.md` | join nodes to a controller |
| `04-add-locations.md` | add controllers to the gateway |
| `05-verify.md` | prove the chain end to end |
| `06-troubleshooting.md` | measured failure modes and fixes |
| `CHECKLIST.md` | the once-per-Mac tick list |

## If it does not work

Work flows one way: **Story Studio → GatewayKH → controller → nodes**. Test it in that
order. `05-verify.md` walks the same path, and `06-troubleshooting.md` lists the failures
that have actually been seen and what fixed them.

Two things that look like bugs but are not:

- Models added from a controller show as **disabled** until you enable them.
- A controller's **Hub** token will not work on the Fleet page. It must be the **Fleet**
  token.
