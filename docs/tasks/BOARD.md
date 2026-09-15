# Studio Hub task board

Updated: 2026-09-15. [Design](../standalone-plan.md) · [Execution plan](../implementation-plan.md).
ST-01 foundation and ST-02 bundled Hub are reviewed and locally integrated; subsequent app phases remain pending. Historical 0.3.0 evidence does not complete these cards.

| ID | Phase / deliverable | Depends on | Status | Owner/session |
| --- | --- | --- | --- | --- |
| ST-00 | Source review and narrowed plan | — | DONE | Current planning session |
| [ST-01](ST-01.md) | A · Runtime/model contracts | ST-00 | DONE | root / implementation/st-01 |
| [ST-02](ST-02.md) | A · Bundled local Hub | ST-01 | DONE | root / implementation/st-02 |
| [ST-03](ST-03.md) | B · Packaged FLUX | ST-02 | READY | Unassigned |
| [ST-04](ST-04.md) | C · Qwen Base | ST-03 | WAITING | Unassigned |
| [ST-05](ST-05.md) | C · VoxCPM2 / OmniVoice | ST-03 | WAITING | Unassigned |
| [ST-06](ST-06.md) | C · Independent Subtitle | ST-03 | WAITING | Unassigned |
| [ST-07](ST-07.md) | C · Per-device cleanup | ST-02 | READY | Unassigned |
| [ST-08](ST-08.md) | D · Native workflows/settings | ST-03, ST-07 | WAITING | Unassigned |
| [ST-09](ST-09.md) | D · Startup/recovery | ST-02 | READY | Unassigned |
| [ST-10](ST-10.md) | E · Fleet/GenStudio | ST-02, ST-09 | WAITING | Unassigned |
| [ST-11](ST-11.md) | F · Qualification/install kit | ST-04, ST-05, ST-06, ST-08, ST-10 | WAITING | Unassigned |

## Status rules

- WAITING: prerequisites unfinished.
- READY: prerequisites complete; available to claim.
- IN_PROGRESS: named owner, start commit and worktree recorded.
- REVIEW: implementation/evidence ready for independent review.
- DONE: reviewed, verified and integrated; exact evidence recorded.
- BLOCKED: concrete unmet requirement and next action recorded.

One coordinator updates the shared board. Workers update only their assigned card/handoff in isolated worktrees. Whole-task dependencies require DONE. Named partial model receipts may unlock only the explicitly identified ST-08.PILOT checkpoint after independent review and integration; they never mark a parent model task complete.

## ST-00 evidence

Baseline: 29d0baa. Deliverables: revised PRODUCT.md, DESIGN.md, README.md, canonical design, execution plan, task cards and three research/rescope-*-2026-09-15.md source inventories.
Proof: 20 Markdown files and 142 local links validated; 11 task cards match the acyclic board dependencies; git diff --check clean. Independent scope_hub_storage review resolved sequencing/storage findings and confirmed no remaining design/handoff blocker. Recorded in canonical progress. Documentation integrated into the local working tree; no app implementation or new release commit.
No application code, runtime, credentials or installed node changed. No new model qualification claimed.

Each card records owner/session, start/final commit, worktree, paths, commands/results, artifact/model/runtime identities, reviewer/findings, limitations and next task.

## Intermediate pilot and final gates

ST-08 may begin after ST-03/ST-07 with native fixtures. ST-08.PILOT additionally
requires integrated, independently reviewed QWEN06 and Whisper Turbo receipts from
ST-04/ST-06; remaining Vox/Omni variants need not finish first.
ST-10 token/contract work depends on Hub and ST-09 software lifecycle, not physical
reboot qualification. ST-11 waits for all complete model/UI/connection tasks and owns
physical login/reboot plus all-model assembled-app acceptance.

## Owner additions after initial review

Complete GitHub/original-host download and copied-folder model setup are now explicit
ST-01/ST-08/ST-11 checkpoints. Location membership, generation data/statistics and an
IP/address + token API suitable for future standalone GenStudio are explicit ST-02,
ST-08 and ST-10 requirements. ST-10.TOPOLOGY is resolved: preserve existing GenStudio-to-location-controller-to-nodes
routing, using existing Hub/fleet tokens. No new gateway or direct node connections.
Independent model/local work continues.

Original-M1/older-OS support is now explicit in ST-01/02/08/09/11. The baseline
candidate is Sonoma 14.0, subject to real per-model and point-release qualification.
Keeping a compatible release working while OS upgrades are deferred is required;
generation during an OS installation restart is not promised.

## ST-01 evidence and next owner

Implementation `072abf6` is reviewed and locally integrated on 2026-09-15. See
[phase receipt](../../qualification/st01.md): 724 passing tests, ten pinned manifests,
real Turbo offline import/relocation/tokenizer and real small-file HTTPS verification.
The retained Image audit's 115 relocation issues are explicitly ST-03.RELOCATE work;
new model inference and physical M1/Sonoma qualification remain later checkpoints.

**ST-02 is DONE.** Implementation `4a5124c` is reviewed and locally integrated.
[Phase receipt](../../qualification/st02.md) records the 0.4.0 native Hub/Voice artifact,
107 project tests, 640 Voice tests, final admission/retained regressions and assembled
process/job proofs. ST-03, ST-07 and ST-09 are READY.
