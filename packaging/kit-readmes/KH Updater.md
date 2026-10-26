# KH Updater — the fleet updater

This folder installs **KH Updater**, the small agent that keeps the three apps on a Mac up to
date. It is for **every Mac that runs any of them** — Story Studio, GatewayKH or Studio Hub —
whether or not that Mac runs the others.

## What it is

KH Updater is one agent per Mac, running as your own user. Every few minutes it asks the
publisher — the Mac Studio — what the newest published release is. The publisher signs that
answer, and the agent refuses anything it cannot verify. When there is something newer it
installs it and keeps the previous copy, so an update that will not start can be put back.

**It is not a download page and it is not a second update button.** You publish on the Mac
Studio; the fleet follows.

## Install on a Mac

Double-click **`Install KH Updater.command`**. It needs no administrator password: everything
lives in your own `~/Library/KHUpdater`.

**Enrolment needs you at the publisher first.** Installing and running are two different things:
a Mac is not allowed to join the fleet until you say so on the publisher. So, in order:

1. On the Mac Studio, open the publisher's own page (`http://127.0.0.1:8790/`) and **open the
   enrolment window**. It stays open for a short while.
2. On the Mac you are setting up, double-click **`Install KH Updater.command`**.
3. It shows the name this Mac will enrol under — on a Hub Mac the Hub's own name, otherwise this
   computer's — and asks you to confirm it or type another. Press Return to accept.
4. It installs the agent, enrols this Mac and starts it. The agent then runs at every login.

If the window is shut the publisher refuses, and the run leaves nothing behind — it says so and
moves whatever it had written to `~/Library/_trash`. Nothing is half-installed, and you can run
the file again once the window is open.

**If the agent is installed and launchd refuses to start it, the install is kept.** The run says
what launchd answered and prints the one command that retries the start, so a hiccup at login does
not cost you a second enrolment:

```
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.khupdater.agent.plist
```

Run it on every Mac. If Studio Hub already installed the agent on a Hub Mac, this file says the
agent is already there and changes nothing — whichever ran first wins, and the two never argue.

## What it updates

| App | What KH Updater does |
|---|---|
| GatewayKH | Installs published releases, replacing the app and stopping its server only when it has to |
| Studio Hub | **Status only** — Studio Hub keeps its own updater, which lets a running job finish |
| Story Studio | **Status only** — Story Studio keeps its own updater |
| KH Updater | Updates itself the same signed way |

`apps.json`, beside the installer, is the list of apps it manages and the bundle identifiers it
looks for. It finds them in `~/Applications` and `/Applications`, records the ones this Mac
actually has, and does not touch an app that is not installed.

## Where it lives, and how to remove it

| Path | What it is |
|---|---|
| `~/Library/KHUpdater` | the agent, its record of this Mac, and its downloads |
| `~/Library/KHUpdater/base/config.json` | this Mac's own enrolment record, readable only by you |
| `~/Library/LaunchAgents/com.khupdater.agent.plist` | the launchd job that starts it at login |
| `~/Library/Logs/khupdater-agent.log` | what it has done |

To remove it: run `launchctl bootout gui/$(id -u)/com.khupdater.agent`, then move
`~/Library/KHUpdater` and that `.plist` to a `_trash` folder. It never deletes: superseded
downloads and replaced copies go to `~/Library/KHUpdater/base/_trash` and
`~/Library/KHUpdater/state/_trash`, which you empty yourself when you want the space back.

**No credential is in this folder.** `khu-agent/publisher.json` holds the publisher's address and
its public key — the key every Mac checks signatures against. It is not a secret; the private half
never leaves the Mac Studio. Each Mac's own enrolment record is minted here, on that Mac, during
its own enrolment.
