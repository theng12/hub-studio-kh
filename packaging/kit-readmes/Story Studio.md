# Story Studio KH — where you work

**Version 1.4.219.** Installer: `Story Studio KH 1.4.219-arm64.dmg`

## What it is for

Story Studio turns an idea into a finished, uploadable episode. It is where you write and
produce:

- ideas and scripts, and the scene-by-scene image prompts;
- images, narration audio, subtitles, and the merged audio track;
- YouTube metadata, the thumbnail, and the assembled video;
- the upload itself.

It installs **Content Packs** you buy from the Content Store — formats, styles, topic
packs, thumbnail recipes, fonts and metadata formulas that extend what it can produce.

It does **not** run any AI model itself and does not hold a list of models. Everything
that needs a model goes to GatewayKH.

## Install

1. Open `Story Studio KH 1.4.219-arm64.dmg`.
2. Drag **Story Studio KH** into **Applications**.
3. It is ad-hoc signed, so clear the quarantine once:
   `xattr -cr "/Applications/Story Studio KH.app"`
   (or right-click the app → **Open** on first launch).
4. Open it.

The DMG also carries a `READ ME FIRST.txt` with the same step.

## Point it at the gateway

Settings → **This Mac** → **GenStudio KH**:

- **Base URL** — the gateway's address. If the gateway is on this Mac, the default
  `http://127.0.0.1:8787` is already right. Otherwise use the gateway Mac's Tailscale
  address and port **8787**.
- **GenStudio API key** — the key GatewayKH minted on its first run (GatewayKH →
  Settings → Copy).

There is a **Test** action next to the key; use it. That is the whole connection — Story
Studio never talks to Studio Hub directly. Studio Hub is a separate app that Story Studio
does not know exists; the gateway is the single address.

## Content Packs

A Content Pack is an independently authored product (a `.sspack`) that **adds to** the
library. The kinds are: topic pack, series formula, visual style, thumbnail recipe, font
pack and metadata formula. Older `channel-kit` and `niche-bundle` packs are retired.

- Packs are bought with credits in the in-app **Content Store** and installed **per Mac**.
- Installed packs live under
  `~/Library/Application Support/Story Studio KH/content-packs/installed/<publisher>/<pack>/<version>/`.
- Only a read-only **recovery pack** ships inside the app; nothing else is bundled.

The Content Store is served by GatewayKH, so the gateway must be reachable and its shelf
linked before packs will appear. If the shelf is not connected, the gateway answers
`503 CONTENT_STORE_NOT_CONNECTED`.

## Where it keeps things

- Data folder: `~/Library/Application Support/Story Studio KH/`
  (override with `STORY_STUDIO_DATA_DIR`)
- Settings: `app-settings.json` · secrets: `app-secrets.json` · packs: `content-packs/`
- Its own server mode listens on **8765** if you turn that on.

## What it needs from the rest of the kit

| It needs | From |
|---|---|
| A reachable gateway address | GatewayKH on the gateway host |
| The gateway's API key | GatewayKH → Settings |
| Models to exist | Studio Hub controllers added to GatewayKH |

Nothing else. If generation fails, the fault is almost always further down the chain —
check GatewayKH's Fleet page first.
