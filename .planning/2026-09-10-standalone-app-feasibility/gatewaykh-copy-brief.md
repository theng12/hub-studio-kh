# GatewayKH — native connection copy alignment

Base b550617; worktree handoff-completion, branch codex/local-readiness-gatewaykh.
User explicitly changed external client from GenStudio to GatewayKH. Root owns
documentation; worker owns **only native/StudioViews.swift** and
`gatewaykh-copy-report.md` in this canonical planning directory.

This is literal UI copy correction, not API migration or new integration behavior.
Read Ponytail and Impeccable/clarify. Root already ran Impeccable context (launcher
permission denied), read PRODUCT.md/DESIGN.md/clarify/craft-floor and inspected the
Connections flow. Preserve all layout, actions, focus and auth/secret masking.

1. Replace owner-facing GenStudio mentions in Connections heading, routing text,
   section heading and controller/agent role explanation with GatewayKH.
2. Do NOT claim GatewayKH owns accounts/billing/assets based only on a name change.
   Use truthful boundary language: GatewayKH sends work to location controllers;
   Studio Hub manages local execution and reports actual capabilities.
3. The identity API still reports global_authority="genstudio" as legacy protocol.
   Keep its actual reported value visible under **"Reported protocol authority"**,
   with **"Not reported"** for nil instead of inventing GenStudio. Do not change
   the backend, persistence, credentials or wire identifiers, or label connected
   unless an actual connection has been verified.
4. Root checked GatewayKH `gateway/providers/registry.py:73-102`: remote work
   uses Fleet token; optional Hub/admin token is for shared voices only. Hub token
   helper should say "This Mac's administrative token. GatewayKH needs it only
   for shared-voice administration; use the Fleet token for work and results."
   Fleet helper should say "Give this to GatewayKH for work and results, or to
   each Mac that joins this controller." Preserve masking/actions/credentials.
   Leave historical source comments alone; no broad code rename.
5. Read final flow and git diff --check. Literal copy needs no source-string tests.
   Coordinator runs fresh native compilation and isolated offscreen Connections
   renders at minimum/default sizes; no live rendering, app replacement or login.

Do not edit any other files, run real connection tests, access private state,
commit/push/deploy or spawn subagents. Use apply_patch. Report exact changed
strings and limits, no claims of end-to-end GatewayKH compatibility.
