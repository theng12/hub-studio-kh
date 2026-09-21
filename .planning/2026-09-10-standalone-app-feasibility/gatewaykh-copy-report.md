# GatewayKH native connection copy report

Base: `b5506178cd0543a4734911c4fdb3c91d21ad271b` in the `handoff-completion`
worktree. Owned source change: `native/StudioViews.swift` only.

## Exact copy correction

- Connections subtitle: `external Studio Hub or GenStudio control.` → `external Studio Hub or GatewayKH control.`
- Node routing: `GenStudio reaches a location controller` → `GatewayKH sends work to a location controller`.
- Hub-token helper now says: `This Mac's administrative token. GatewayKH needs it only for shared-voice administration; use the Fleet token for work and results.`
- Fleet-token helper now says: `Give this to GatewayKH for work and results, or to each Mac that joins this controller.`
- Controller section heading: `Studio Hub & GenStudio` → `Studio Hub & GatewayKH`.
- Protocol identity label: `Global authority` → `Reported protocol authority`; the value now displays the reported string unchanged, with `Not reported` only when the API value is nil. The legacy wire value `genstudio` was not changed.
- Controller boundary copy now says that GatewayKH sends work to this location controller; the local Hub records/claims jobs and reports its actual capabilities.
- Agent boundary copy now states that this Mac creates locally and does not contact GatewayKH directly.

## Preserved behavior and limits

Layout, buttons, actions, token masking/copy behavior, credentials, role controls,
API calls, persistence, and legacy source comments were left unchanged. This is a
literal owner-facing copy correction; it makes no end-to-end GatewayKH compatibility
claim and does not rename protocol or persistence identifiers.

Validation is limited to source inspection and `git diff --check`; no native build,
offscreen render, live connection, login, or service was run in this worker. Root
should run the fresh native compile and isolated Connections renders described by
the copy brief.
