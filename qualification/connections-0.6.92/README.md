# Receipt — the Connections page at 0.6.92, rendered against the live Hub

`STUDIOHUB_RENDER_LIVE=1 bash native/render-page.sh connections .build/renders-0.6.92 1920x1053`
run on 2026-09-28 against the **installed** Hub `0.6.92 / 0cb6e73` on 47873. The script printed
`connected to the running Hub at http://127.0.0.1:47873`, so this is the real page against the real
service, not a mock.

**What to look at, and what it does not show:**

- **Node identity** is drawn as its own row, separate from the name, with the sentence saying that
  renaming changes only the label and never re-registers the Mac (H12, owner decision 9). It reads
  *Not reporting* here because the render harness does not start the worker services, so the identity
  read has no answer — which is the honest state, not a defect.
- **Group — Unknown** with *"This Mac creates on its own. Nothing else can send it work and it
  contacts nothing else."* is the standalone case, correctly worded.
- The role picker's three roles keep their one-line explanation.

**Not visible in a still**, and therefore pinned by tests rather than by this image: the removal
banner and its sentence, the retired-controller banner, the confirmations on role change / leave /
remove, the Promote / Hand over / Rotate results with their per-Mac lists, and the pin-clearing
behaviour. Those predicates and sentences live in `native/StartupRecovery.swift` (`FleetCopy`) and
the contract suite, precisely because a screenshot cannot hold them.

## Also here: the same page at 0.6.93

`connections-0.6.93-1920x1053.png`, rendered the same way against the installed **0.6.93 /
`c7feed7`** after the rehearsal's two handover fixes. The differences from the 0.6.92 render are in
behaviour rather than layout — the promotion and handover results, and a node's own registration
with a new controller — so the still looks the same and the picture is here as the record that the
page still renders on the released build.
