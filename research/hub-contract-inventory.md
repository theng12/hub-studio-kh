# Hub contract inventory

Read-only inspection on 2026-09-10, independently performed by the contract-inventory agent. Primary review confirmed the Git identity implementation, fixed repository map, role gate and pinned-target documentation directly. Source baseline: `source-checkouts.json`.

| Concern | Evidence | Implication |
|---|---|---|
| Discovery and mutable roots | [registry.py:22](/build/pinokio/api/studiohub-mac/app/backend/registry.py:22), [registry.py:204](/build/pinokio/api/studiohub-mac/app/backend/registry.py:204) | Current sibling discovery assumes Pinokio's API tree; standalone needs explicit bundle and writable roots. |
| Process lifecycle | [control.py:23](/build/pinokio/api/studiohub-mac/app/backend/control.py:23), [control.py:76](/build/pinokio/api/studiohub-mac/app/backend/control.py:76) | Current controller invokes pterm and Pinokio URLs. Use app-owned processes in the new project. |
| Service collisions | [startup_services.py:26](/build/pinokio/api/studiohub-mac/app/backend/startup_services.py:26), [install_service.sh:11](/build/pinokio/api/studiohub-mac/install_service.sh:11) | Existing ports are Hub 47873, Image 47868, Voice 47870. Preserve contract expectations where necessary; use distinct app service labels and safe collision handling. |
| Runtime identity | [main.py:315](/build/pinokio/api/studiohub-mac/app/backend/main.py:315) | VERSION plus live Git HEAD currently supplies identity. Embed truthful immutable metadata; upstream commits alone do not identify locally adapted runtime code. |
| Updater preconditions | [auto_update.py:172](/build/pinokio/api/studiohub-mac/app/backend/auto_update.py:172), [auto_update.py:605](/build/pinokio/api/studiohub-mac/app/backend/auto_update.py:605) | Existing Git/remote/Pinokio-root checks reject normal app bundles. Do not bypass them for legacy nodes. |
| Release repository map | [release_reconciliation.py:39](/build/pinokio/api/studiohub-mac/app/backend/release_reconciliation.py:39) | Fixed Hub/Image/Voice GitHub repo identities, SemVer and 40-hex commit fields require an explicit mapping/contract decision for standalone releases. |
| Managed release capability | [fleet_auto_updates.py:58](/build/pinokio/api/studiohub-mac/app/backend/fleet_auto_updates.py:58), [integration brief:62](/build/pinokio/api/studiohub-mac/studiohub_genstudio_integration.md:62) | Exact managed update behavior is required; an app must not advertise managed_exact_commit merely because it embeds a commit string. |
| Authentication | [contract:16](/build/pinokio/api/studiohub-mac/CAPABILITY_CONTRACT.md:16), [auth.py:381](/build/pinokio/api/studiohub-mac/app/backend/auth.py:381) | Machine endpoints require header credentials even on loopback; cookie, password and query tokens are invalid. New installation gets new credentials. |
| Roles | [control_plane.py:26](/build/pinokio/api/studiohub-mac/app/backend/control_plane.py:26), [main.py:4096](/build/pinokio/api/studiohub-mac/app/backend/main.py:4096) | The product name standalone is unrelated to Hub's standalone role. Managed release writes require controller/agent roles as appropriate. |
| Enrollment | [enrollment.py:493](/build/pinokio/api/studiohub-mac/app/backend/enrollment.py:493), [architecture:57](/build/pinokio/api/studiohub-mac/CONTROLLER_ARCHITECTURE.md:57) | A site controller and an agent joining a site are different flows; do not make every node a new controller. |
| Execution authority | [execution_identity.py:125](/build/pinokio/api/studiohub-mac/app/backend/execution_identity.py:125) | GenStudio owns global job/attempt/idempotency/fencing identities. Hub validates them and site identity. |

## Specific integration acceptance cases

1. `/api/version` and `/api/health` attest the actual bundled implementation and selected component identities without runtime Git.
2. `/api/hub/capabilities` validates schema `studiohub.site-capabilities` version 3 with correct site/controller identity. Valid header credentials work; absent, incorrect, query and cookie credentials fail without credential logging.
3. Freshness, readiness, drain state, owner approval, revision pinning and model limits continue to govern availability. Image and Voice cannot double-count a Mac's shared capacity.
4. Image jobs use `/api/hub/jobs`; voice references use authenticated multipart staging, checksums, expiring asset IDs, normal polling/cancel and restaging after expiry.
5. Duplicate request delivery, stale fencing and mismatched site identity preserve existing semantics. Capability poll failure does not issue jobs, charge/refund, or change execution authority.
6. Mixed-fleet fixtures prove legacy nodes keep Git/Pinokio maintenance. Packaged nodes reject unsupported update operations honestly.
7. Before claiming managed-release support, test exact/idempotent release-intent PUT, sequence/content rejection, activation, job polling after response loss, exact runtime attestation, degraded targets and retryable failures. GenStudio must not call agent-only managed-update routes or moving-main launchers.
8. Test new controller setup and joining an existing controller separately, with stable identities, private controller endpoints and single-use enrollment credentials. Live enrollment remains a separately authorized pilot action.

The integration brief's initial qualified targets (Image 1.30.1, Voice 2.3.0) differ from current source checkouts (Image 1.32.1, Voice 2.7.2). Neither is automatic qualification of a new packaged runtime. Record the exact selected implementation and rerun its qualification.

No existing repositories, running services or credentials were modified by this inventory. The inspection host runs macOS 26.6.2; Sonoma compatibility remains untested.
