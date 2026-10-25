# KH Updater — not ready yet

**There is nothing to run in this folder.** It has no installer on purpose, so that nobody can
start a half-configured one.

KH Updater is the small agent that keeps the three apps on a Mac up to date from the publisher —
the Mac Studio. Every Mac it runs on has to check the publisher's signatures, and that needs the
publisher's public key. **That key has not been created yet**, so there is no `publisher.json` to
put here and nothing for a Mac to trust.

## What has to happen first

On the Mac Studio, once:

```
cd <the kh-updater checkout>
./build.sh build
packaging/publisher-init.sh --tailscale-ip "$(tailscale ip -4)" --backup-to "/Volumes/<a drive>"
```

That creates the signing key (which never leaves that Mac and is backed up once to a removable
drive), writes `~/Library/KHUpdater/publisher.json`, and prints the public key. After that the
drives can be prepared again with that file, and this folder is replaced by the real one: a
README, `Install KH Updater.command`, the apps config and the agent kit.

## Why no installer at all

An installer with no publisher record in it would install an agent that can never enrol — it would
sit on the Mac failing every few minutes and looking, from the outside, like a Mac that is simply
not reporting. One sentence here is a better answer than that.
