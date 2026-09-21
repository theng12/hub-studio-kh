# Progress

## 2026-09-21 — local verification / GatewayKH continuation

[MEASURED] FINAL LOCAL GATE: clean candidate `936b3e5` passed 641 root tests
(7 skips, 428.429s), nested Voice722/19 and fresh Hub108. Native build, 21 final-app
release/phonemizer checks and four-worker model-free startup/auth/owned shutdown
passed. Exact app tree `06e881146e69e88097df71291569ffa873acf541381dc3fb8010df1ec34024e7`.
Integration: https://github.com/theng12/Studio-Hub-KH/pull/13 (merge state/hash
authoritative). Evidence-only child preserves the exact tested source identity.
See `qualification/local-readiness-2026-09-21.md`, including the failed first gate,
fixture corrections, unproven intermittent peer observation and remaining warnings.
Next after merge: R04. Its four bypasses reproduced again on this final candidate;
they are not repaired by this batch. No installed-app/model/fleet/deployment changes.

Owner requested runnable local work and changed the external integration target
from GenStudio to GatewayKH. R03 claimed at PR #12 merge `b550617`, using preserved
handoff-completion worktree on new branch `codex/local-readiness-gatewaykh`.
Baseline Hub suite: 85 passed, two upstream deprecation warnings, 1.65 seconds.
Root owns canonical records/review/integration; worker scope is in r03-brief.md.
Ruling: GatewayKH is the current target; retain legacy identifiers until its actual
contract is inspected, rather than breaking compatibility through a global rename.
Cost if wrong: a later explicit migration may be necessary; compatibility is not
claimed by this naming update. Graphify CLI remains unavailable; direct inspection.
No protected model library, installed app, production connection or enrollment changes.

R05 independent native-build slice dispatched concurrently (native/build.sh,
render-page.sh, shared SDK helper and shell behavior tests only); no R03 overlap.
Root R04 boundary probe confirmed paused-model, disabled-studio, exhausted-storage
and reserved-machine requests still forward through the fleet proxy (HTTP200, one
worker request each). Full two-Hub qualification and repair remain; see r04-preflight.md. Integration
remote still b550617; GitHub workflows count zero. Active docs now name GatewayKH.

[MEASURED] R03 follow-up source is frozen for re-review after four reproduced
independent findings: invalid JSON retry, recovery losing malformed-receipt
classification, fractional byte-count coercion, and worker-local idempotency not
protecting cross-worker retry after malformed acceptance. The smallest repair
fences unreadable successful receipts, including cancellation races, using existing
uncertainty helpers. Root fresh Hub suite: 108 passed, two upstream warnings.
Full packaged gate and integration remain pending at this checkpoint.

[REPORTED / independently checked] Sol/high reviewer `review_r03_integrity` found
no remaining actionable spec/quality issues on the frozen eight-file diff
`a2f4022027a57251fb30164901db6b8a8931d04445fd0e5ed53bc424b13cf915`.
Reviewer reruns: focused integrity10, Hub108, standalone10, real Voice replay/conflict1
passed. Root separately reran Hub108. R05 Terra review was also clean; root reviewed
the literal GatewayKH copy diff against its pinned counterpart's token contract.
Proceed to a clean source commit and separate staged artifact; no qualification
claim extends to the installed app, real models, physical fleet or GatewayKH E2E.

[MEASURED] R05 native-build slice has clean independent review; root SDK26.5 build
passed boundary/API/lifecycle gates and optimized arm64 compilation. Eight isolated
build-script tests passed. GatewayKH copy-only Connections renders were inspected
at 1040×680 and 1920×1053 in disconnected/offline state; token/identity states and
live control remain unqualified. Impeccable informed clear token-scope wording,
not a redesign. GatewayKH's three offline checkpoint tests passed; contract gaps
remain ACK, upstream cancellation, Music and reference/recovery qualification.

[MEASURED] First clean candidate `fb36c89` passed packaged four-worker model-free
startup/auth/owned shutdown and 21 release/phonemizer checks, but its full gate
failed: 641 tests, three failures, seven skips (449.455s). Two duplicate legacy
Voice proof fixtures returned incomplete receipts; Luna aligned only those two
fixtures with the real serializer. Root independently reviewed their diffs and
reran both proof modules: eight tests passed (15.401s); assertions were preserved.
The third failure was a 45-second shared-voice peer reachability miss. It did not
reproduce alone (one passed, 13.68s) or in its complete class (six passed, 61.42s).
No timeout/admission changes were made; its cause is unproven, not declared fixed.
One fixture-process ResourceWarning was emitted; its named PID had exited when
checked. A clean second candidate/full rerun is required before integration.

## 2026-09-21 — ordered remaining-work execution

[MEASURED] Final wave-1 gate is green at clean product `7ec9f89`: 632 tests OK,
seven skips, 415.052s; nested Voice722/19, Hub85; native build/boundary/API/lifecycle
pass; 21 release/phonemizer and five original probes pass. Model-free four-worker
startup/auth/shutdown and unchanged bundle pass. Exact candidate tree is
`07c4d895b795513c9c5f8d1d073d34c90a31a40e5bd950f06f797bfa85d8cb25`.
R01 was committed first at `0e11671`, then R02 at `7ec9f89`. Evidence:
`qualification/handoff-completion-wave1-2026-09-21.md`. Reviewed integration is
tracked by https://github.com/theng12/Studio-Hub-KH/pull/12 (merge state/hash
authoritative there). Evidence-only child retains exact product source identity.
Next after integration: R03; no worker dispatched for it yet. Root reproduced
unknown Voice state and missing live/reconcile output URLs publishing persisted
`done`/public `succeeded`, plus truncated-WAV acceptance. No R03 product fix yet.
Workflows checked zero; no hosted compute/deployment or installed-app change.

Earlier checkpoint: R01 and R02 source/test repairs independently reviewed with no actionable
findings. Root separately re-ran full native build/boundary/API and lifecycle
harness: all passed (`.build/root-native-final.log`). Source is frozen for ordered
R01 then R02 commits and a clean current-source artifact gate before publishing.
The first full Python run used the older explicit artifact; its single identity
failure remains unresolved until the new artifact/run, not waived. Impeccable
informed native offline/concurrent-state review without visual redesign; context
launcher was not executable, so existing PRODUCT.md/DESIGN.md were read directly.

Review rulings: R02 explicit corrupt deadlines remain preserved rather than
silently deleted; missing deadlines retain prior expiry behavior. R03 preserves
stable-ID replays and generated-but-unverified compatibility. Independent review
declined broad changes to those contracts; root agrees for the reasons and costs
recorded in r02-report.md and task_plan.md. No deferred minor code findings.

Owner authorized planning all remaining work and starting top to bottom. Root
extended the existing `task_plan.md` with R01–R10, ownership, dependencies, proving
checks and explicit physical/deployment gates; no parallel planning system was created.
Base integration `1267a51`; isolated worktree `handoff-completion`, branch
`codex/handoff-completion`. Native build/boundary/API baseline passed; reference,
purge and standalone baseline passed 18 tests with two dependency deprecation warnings.

Preflight: R01 native and R02 execution-assets have disjoint source/tests and can
run concurrently in the first wave. Broker (R03/R04), shared native (R01/R06/R08/R09)
and build/staging/model boundaries are serialized as recorded in the plan's
ownership table. Current Graphify CLI is absent and the old Voice graph cannot
support these paths; use verified current-source callers. Existing artifacts,
installed app and protected model library remain unchanged.

R01: IN PROGRESS — native health ownership/rejoin reproduction then minimal repair.
R02: IN PROGRESS — root inline fallback after second worker/follow-up dispatch
failed with agent-service task limit; executing-plans applies within this canonical
ledger. Real authenticated endpoint regressions reproduced 20 failures/1 error
(accepted invalid timing, invalid stored expiry); purge regression reproduced
4 deletions instead of 1 for empty-container/null deadlines. Minimal shared parser
now refuses corrupt stored expiry without mutation, including purge skip-invalid.
Ruling: distinguish explicit invalid null/container from absent deadline; preserve
the latter's existing expired-cleanup contract. Cost: corrupt explicit deadlines
need deliberate repair/removal rather than automatic deletion.
R03–R10: queued; missing physical hardware/authority is not a completed test.

R02 implementation: focused 19 passed (28 child HTTP tests), broader isolated Hub
85 passed. Independent `music_recovery_read` reviewer traced callers, re-ran 19
focused checks and found no actionable findings. Ordered integration waits for
R01 native lifecycle proof/review; no DONE card or installed-app change yet.
R03 read-only preflight completed to distinguish malformed terminal receipts from
the existing done-but-unbillable metadata fallback and stable-ID idempotent repost
contract. No broker implementation is authorized from an unverified assumption.

[MEASURED] Broad preliminary Python run: 632 tests in 388.394s, seven skips,
one source-identity assertion failed because the explicitly selected prior
reviewed artifact names `c9eadca`, while this branch names `0ec3b3b`. All other
assertions passed; nested Voice 722 passed/19 skipped, Hub 85 passed. This is
not a passing final gate: rebuild from the reviewed final source and rerun the
source-bound release checks without weakening the assertion. Local log:
`.build/r02-full-regression.log`. Dependency/resource warnings remain visible.

Follow-up framework decision: owner accepted native SwiftUI/AppKit with WKWebView and disclosed no Apple Developer account. Verified Apple membership and Gatekeeper guidance; recorded that paid enrollment is unnecessary for local development/internal testing, while Developer ID/notarization need membership and transferred internal builds may require normal per-app approval. No runtime changes, login registration, enrollment or distribution performed in this discussion turn. Updated the existing design and owner direction only; documentation diff checked.

2026-09-10: Read requested skills. Created isolated planning project at /build/Projects/studiohub-standalone. Initialized PLAN_ID 2026-09-10-standalone-app-feasibility. No source repositories changed. Parent Pinokio instructions inspected in earlier work; launcher creation rules do not apply to this standalone planning project. Existing source graphs will be copied into this project before querying so generated research state stays isolated. Optional first-version Studio scope clarification sent to owner.

Saved source commits/versions/status without modifying checkouts. Graphify reflection and runtime query completed against isolated copied graph. Read-only Hub contract inventory delegated to Luna high, deliverable source-linked assumptions and acceptance tests; no implementation.

Completed source inventory, architecture and staged qualification plan. Consumed independent contract inventory and confirmed key findings against source. Recorded fixed repository identity, separate Hub roles, and honest managed-release support constraints. Created project README and scoped AGENTS instructions. No application source, dependencies, services or credentials copied or changed. Final checks cover document links, source checkout identity/status preservation and explicit planning root resolution. Planning complete; implementation not started.

Implementation started with owner authorization. Initialized isolated local repository on implementation/voice-runtime (1114919), adopted tracked Voice source only at cfdd5e3. No remote configured. Delegated portable_python_build (Sol high): owns runtime build scripts/tests and runtime/license inventory; completion proof real build log + relocation audit. Delegated voice_path_inventory (Luna high), then scoped implementation: owns adopted backend paths and standalone tests; proof isolated backend tests with external state. Shared base cfdd5e3; root owns launcher/staging and planning.
Root launcher tests first failed for absent implementation; after implementation three path tests passed after fixing macOS /var symlink canonicalization in expected paths. Staging validation test first failed for absent module, then passed; four focused tests now pass with system Python (test tool only). No application/runtime qualification yet.

Independent root launcher/staging review assigned to existing standalone_contract_inventory agent. New review agent dispatch and old sdk review followup were rejected by agent thread limit; reused available completed agent successfully. No work was lost.

Base runtime staged successfully at dist/Voice Base Proof.app. Real isolated worker started and health/catalog/update status passed. Smoke expected a 401 for unauthenticated loopback update; actual409 matches existing Voice loopback-exempt local UI authentication, then unavailable updater rejection. Corrected smoke expectation; remote-machine auth remains covered separately by adopted fleet auth tests. Worker was cleanly stopped and logs retained.

Base smoke2 passed: real API startup from Initial.app and Relocated Folder with Spaces/Voice Proof.app, private external state, credential file0600, local updates rejected409, managed_exact_commit false, full bundle digest unchanged on both launches. macOS26.6.2 only; no generation or clean OS proof yet. Independent reviewer identified version consistency, complete payload identity, stale UI and staging fixture gaps. Agent handles version/UI; root added complete payload hash plus full tree checksum sidecar and tracked-only frontend/audit copying. Six root tests pass, including full staged fixture rejecting untracked credentials and detecting worker payload change.

Root FFmpeg source build began from repository cwd by mistake, creating only generated ffbuild/config.log before discovery. Stopped the exact configure PID, preserved generated files under .build/ffmpeg-misplaced-configure, restarted from dedicated .build/ffmpeg-build. No tracked files or other work removed.

Full Python dependency sync underway in builder-owned .build/voice-runtime-full.incomplete. Root concurrently builds FFmpeg9.0.1 from verified source with macOS14 deployment target, static libs/no autodetect/no network; configure completed and make-j4/install running in .build/ffmpeg-build. Isolated pilot model copies now present under .build/pilot-models; qualification/model-inputs.json records each file SHA/size. Existing source caches were read-only. Root smoke helper can execute Qwen then Whisper via API from relocated artifact in HF offline mode; physical network-off test remains separate.
Root scoped independent review completed after tracked backend leak regression red/green; five root unit tests pass. New model execution smoke is pending actual runtime and media tools.

Reviewed base artifact restaged with payload hash and tracked source allowlists. .build/base-reviewed-smoke/report.json passes two real startups, move into spaced path, unchanged bytes+modes hash, sentinel inherited credential stripping, selected model storage, app version0.1.0 and unavailable managed updates. No generation in that run.

Independent backend review requested loaded-process manifest snapshot, health release metadata, truthful idle blockers and actual loopback binding. Root connectivity smoke reproduced legacy0.0.0.0:47870 metadata against an actual ephemeral loopback server. Root launcher now prebinds one Uvicorn socket, passes actual STUDIOHUB_BIND_HOST/PORT, strips PINOKIO_* env; path agent fixes corresponding backend/tests.
During FFmpeg restart, root initially selected an unrelated `make test` PID from an insufficient process listing and sent SIGINT; verified it remained running immediately and afterwards, and notified owner. Then verified own make-j4 parent command and cwd via lsof before stopping exact FFmpeg PID45643. No further signals to unrelated processes. Future process control requires exact task-owned command and cwd evidence.

FFmpeg9.0.1 build completed with prefix=/ and DESTDIR installation; both binaries run, arm64/minOS14.0 and system-only dylib links verified. Generated misplaced src symlink was removed only after verifying its exact target inside .build. Base server verified smoke passed correct loopback/actual-port metadata, clean interpreter environment, external credential0600 and unchanged relocated bundle. Runtime audit reviewer found dylib/rpath/symlink/version-normalization gaps; builder assigned fixes before finalization.

Full-runtime regression initially failed23 tests due old cacheenvironmentfixtures, a startup timeout and sourcechangelog selection. Correctedfixtures retainproductionguards; final637passed2skipped, runtimebytesunchanged (log .build/backend-full-tests-fixed.log). Real smoke caught roothelper job_id/publicid mismatch;2smokecontracttests nowcoverid/refchecks. Rootcopiedmodelrefs erroneously included newline; correctedexact40byteHFrefs aftertokenizerprobe demonstratedrepoIDlen1/localpathlen51866. No transcriptionbackendpatchneeded. Relocateddevelopmentartifact generated5.04secaudio andmatchingWhispertranscript in55.207sec total (.build/model-retry2/report.json). Finalscopedruntime removesF5-specific TorchCodec/FFmpegABI dependencytree;307nativebinaryaudits pass, pyopenjtalkdebugprefix cleanup remains beforefinalartifactrepeat.

Final local implementation checkpoints: 7f98089 and b21b263. Rebuilt pyopenjtalk without embedded build prefixes and normalized 24 stale dylib IDs with build-time ad-hoc signatures. All 284 native binaries pass the final audit. The runtime has 137 installed distributions (136 resolved), 32,784 manifest records, and 243 collected license references; no writable runtime entries or bytecode files remain. A macOS rename failure required briefly permitting owner writes on the runtime root, then restoring its read-only mode; the file manifest remained unchanged after both moves.

The final combined regression suite passed 665 tests with five upstream warnings. Staged dist/Studio Hub Voice Proof.app from clean commit b21b263. The final smoke passed two launches, exact release identity, model hash/reference validation, Qwen speech and Whisper transcription, with unchanged app digest 84bf7817…abe01. Saved durable qualification JSON and the report. Physical Sonoma, physical network isolation, signing, deployment, enrollment, Hub/Image, the native window and GenStudio integration remain unclaimed.

Cleanup removed four superseded generated apps and their sidecars from dist (2,662,510,614 logical bytes). Kept the final app, its checksum, build inputs, imported pilot models, failed-run logs and final smoke evidence.

Final source verification found concurrent Voice updates: the read-only reference checkout advanced from 154e739 to b0df180 through PRs #13/#14 and remained clean. Hub/Image stayed unchanged and clean. Preserved the updated checkout and retained the standalone adoption at its original exact baseline; no automatic source refresh was performed. Recorded final observations in research/source-observations-final.json.

Exit checklist: standalone scope and accepted plan re-read; no launcher files or examples apply to this app proof. Local tests, native audits, relocation, model execution, checksum correspondence, review closure and cleanup verified. Existing Pinokio flow preserved; concurrent source updates recorded without replacement. All owned build/test workers exited. No external publication, hosted compute, signing submission, enrollment or deployment occurred. Clarified the project guidance to distinguish prohibited wholesale live-cache adoption from the already-planned import of selected validated model assets.

Six-function continuation: owner approved proceeding and confirmed Audio, Transcription, Image, Music, Video, Chat. Updated accepted plan in place; old three-component exclusion superseded. Source inventory delegated to function_scope_inventory (Luna high, baseline62854ff; proof source-linked report and clean reference statuses). Native readiness/staging delegated to native_packaging (Sol high; proof focused Python tests); root owns native Swift shell. Build tools found: CommandLineTools swiftc. Swift boundary test first failed because ShellCore did not exist; first implementation compiled, then the test exposed URL directory/trailing-slash comparison, corrected to compare canonical paths. Root UI compiles targeting macOS14. No existing Pinokio services changed.

2026-09-11 native preview: SwiftUI/AppKit + retained WKWebView now implements six-function navigation, external model selection, native save/upload, menu-bar reopening and explicit stop/quit. Audio and Transcription share the packaged Voice worker; the other four and bundled Hub remain pending. Real development GUI generated a 6.16-second WAV, saved it through NSSavePanel and transcribed the re-uploaded file to the expected sentence. Login startup, microphone and autoplay were not exercised. Independent lifecycle review added scoped parent-loss cleanup for both the server and its isolated native child; actual Uvicorn re-raises SIGTERM before outer finally, so readiness cleanup now also runs in shutdown. A real Uvicorn regression failed before and passed after the fix. Final regression initially exposed a subprocess test PYTHONPATH assumption and a stale 0.1.0 expected-version fixture; fixed both fixtures without weakening production behavior. Native boundary tests and optimized macOS14-target build pass. Final artifact/regression qualification follows.

Verification environment correction: narrowing PATH removed the development-only Node executable needed by frontend regression tests. Restored normal development PATH and set STUDIOHUB_TOOLS_DIR explicitly to the bundled runtime for real FFmpeg checks. No runtime dependencies were changed. These are test harness requirements, not target-node dependencies.

Final native preview: 676 regression tests passed with five upstream warnings; native boundary checks/build passed. Clean implementation commit5b4ddf4 staged as dist/Studio Hub Preview.app0.2.0. Final native startup/health and parent-loss shutdown passed with readiness removed. Final relocation smoke passed Qwen/Whisper and unchanged digest81d3e371…20d75. GUI automation opened one extra unconfigured first-run instance, which was stopped; both app processes and their worker exited. Retained resource_tracker semaphore-cleanup warning in smoke log, without claiming long-run lifecycle qualification. qualification/native-preview.md and JSON evidence distinguish prior development GUI execution from final artifact tests. Hub/Image, Chat/Music/Video and external qualification remain pending. Existing Pinokio source and services were not changed in this continuation.

Exit audit correction: cleanup found two pre-fix GUI helper processes and four old0.1.0 smoke native children, all orphaned under this project. Verified exact executable/cwd and stopped only those PIDs. Corrected historical all-workers-exited claim in qualification/voice-runtime.md. Final0.2.0 smoke had no remaining children. Removed two disposable app copies (~3.4GB allocated), retained final and historical artifacts and all evidence. Final delivered-app checksum matches. Standalone/plan instructions, source scope, local checks, review closure and no-publication restrictions satisfied; no launcher changes apply.

2026-09-11 redesign owner correction: separate Audio and Transcription as real workspaces; replace inherited UI; centralized models/settings/management. Owner chose node-operation home (jobs/engines/model readiness). Baseline53f414a clean. Read standalone instructions and canonical plan; no Pinokio launcher work applies. Reusing Impeccable, planning-with-files and Ponytail. Existing Graphify is stale Voice-only; new Swift shell and concrete current API contracts are inspected directly. function_scope_inventory assigned read-only endpoint contract report at53f414a. Root owns native redesign. Design direction follows native resource-manager source-list/detail grammar; seed090157d2 selected candidate6. Existing PRODUCT/DESIGN reuse-legacy directives superseded by explicit owner redesign.

2026-09-11 native redesign implementation: owner clarified local asset creation is primary; API and Studio Hub/GenStudio control are optional secondary functions. Replaced WKWebView and legacy route switching with native Overview, six creation destinations, Models, Jobs, Connections and Settings. Audio and Transcription have separate drafts, choices, input and result state. API client preserves existing backend model/resource/deletion guards; no worker source or finalized runtime changed. Native client delegated to native_packaging at53f414a with Foundation fixture tests as completion proof; final read-only native review delegated to voice_path_inventory. Full packaging unittest discovery passed28tests in8.462s. Real GUI Qwen jobc56f0f41d938 generated7.6s WAV; native save/import/Whisper/export returned expected sentence and SRT. Evidence qualification/redesign-gui.json. These actions used a disposable development harness sharing immutable0.2runtime resources, not the final0.3artifact. Quit removed owned GUI and worker.

Build/visual corrections: installed SDK27 SwiftUI State macro plugin missing; targeted SDK26.5 build passes while preserving arm64/macOS14 deployment target. Concurrent source edits invalidated two intermediate builds; builds now wait for stable files. Native split navigation intrinsic resizing clipped headings and expanded content; replaced it with fixed source-list/detail layout and disabled NSHosting intrinsic window sizing. Sidebar width is applied after its status inset. Removed word-timestamp UI until native word-level review/export exists; Text/SRT/VTT remain. Native Save panel treats slash-containing filename input as a literal sanitized name; moved only the generated test export from Documents to intended .build path and used filename-only subtitle save. No Pinokio launcher/source/service edits; no credentials, model deletions, enrollment, hosted compute or login registration.

Final redesign review/verification: read-only review found stale token-save success and unsafe existing log handling. Explicit save success is now epoch-checked; held-response test covers reconnect. Native log opens no-follow, requires a regular file, enforces0600 and appends; focused tests cover permission normalization, content preservation and symlink rejection. The API lifecycle tests additionally reproduce/prevent old multipart uploads reaching a replacement worker and duplicate polling after overlapping connects. Real native layout inspection passed default1380x900 and minimum1040x680 for Audio, Transcription and Models; Settings/Jobs/Connections/Overview inspected. Audio Script label now resists vertical compression. Draft and latest transcript restored after restart; central installed filter and model details match the2actualcachedmodels. Broad regression first used the wrong cwd, causing relative pytest exclusions for absent upstream tools to miss; rerunning from components/voice with its existing pytest.ini passed676tests,5warnings,37.89s. Finalized runtime was not modified.

Final 0.3.0 preview: clean implementation 9efb6f0 staged as dist/Studio Hub.app (1.7 GB). The final native window restores Audio/Transcription state; the minimum-size Script label and controls are visible; worker health reports the exact release. Quit stops the worker and removes readiness. The relocated copy passed real Qwen/Whisper execution in 29.54 seconds, with startup times of 8.312/1.986 seconds. Digest 7634717f…b4d914 matches the staged artifact and remains unchanged. Qualification JSON records launch, release, smoke and transcript evidence. The upstream resource_tracker warning about cleaning 13 semaphores remains; direct PID and process-path checks found no remaining supervisor/model processes. Removed the disposable development harness and relocated copy safely; retained final and historical artifacts. No Git remote is configured, so integration consists of local implementation and evidence commits. Exit check: standalone instructions and canonical plan followed; review findings closed; native and regression checks completed; no Pinokio launcher changes apply. Existing installations retain their flow. Remaining local engines, optional controller integration, physical Sonoma and distribution qualification are explicit.

2026-09-14: owner requested stocking khmerttsopensource/khmer-tts, then clarified personal use only. Baseline clean; standalone scope and canonical plan reviewed. Downloaded the pinned revision c9380a9d1c817f2b21980d54f29f7a61b0d626f5 into model-library/khmer-tts/, outside the app, Git and disposable .build outputs. Saved 11 inference/source/attribution files, 332,197,043 bytes total. Published LFS SHA-256 matched for weights; reread and verified every saved checksum, JSON configuration and safetensors header. research/khmer-tts-stock.json records exact files/revision; README links the stock record. No dependencies installed, upstream code executed, model inference run, app catalog enabled or Pinokio installation changed. No publication or user access was enabled.

2026-09-14 research-only handoff: owner deferred SSD follow-up until they announce VoxCPM2 8-bit download completion; explicitly no download polling. Recorded current SSD state and per-drive refresh order in research/ssd-refresh-handoff.md. Investigated Yume 1.5 MLX 4-bit for 24 GB I2V in research/yume-i2v-24gb.md; current loader expands BF16 T5 weights to float32 (~22.72 GB parameters), so 24 GB remains unqualified. No runtime changes, weight downloads, tests or generation. Preserved pre-existing graph query-stamp modification.

2026-09-15 rescope planning started: owner requests a simpler bundled app: current fleet FLUX.2 klein, Qwen3 TTS 0.6B/1.7B Base, VoxCPM2 4-bit/8-bit/full, OmniVoice full, separate Subtitle Studio with three Whisper choices, Hub controller, automatic startup and bounded generated-output storage. Owner requests source-backed assessment, build order, labeled agent checkpoints and session handoffs before further implementation. The ambiguous age phrase "30 deletion after generation" is pending clarification. Existing progress additions, graph stamp and research files are pre-existing and preserved. Baseline 29d0baa; no source/runtimes/dependencies/old nodes changed. Plan resolver lacked executable permission; invoked its existing script through bash without changing permissions. Root owns shared planning documents; independent read-only inventories will feed the revised canonical plan.

2026-09-15 rescope clarification: owner confirmed 50 GB combined generated files per device and 30 days after generation, either condition first; all operating defaults editable, standalone local use plus protected Hub token for GenStudio. Three source inventories completed and reviewed. Wrote narrowed canonical design, PRODUCT/DESIGN/README updates, eleven task cards, board and execution plan. No new app implementation. A document-write attempt hit a stdin encoding error before creating files; reran with ASCII-escaped JSON successfully. Independent final planning review pending.

2026-09-15 rescope complete (ST-00): independent scope_hub_storage review found four planning issues, all resolved and closure verified: software startup no longer waits for physical reboot hardware before connection work; native pilot can consume independently reviewed QWEN06/Turbo receipts before all Vox/Omni variants; changing managed output root remains within quota while explicit exported copies are excluded; proposed five-minute cadence is distinguished from inherited behavior, with admission authoritative. ST-11 owns final all-model/hardware proof. Validated 20 Markdown files, 142 local links, eleven task cards with matching acyclic dependencies, and clean git diff --check. Initial checker treated a URL-encoded space as a broken link; decoded it and verified the real manifest exists. ST-00 DONE; ST-01 READY. Reviewed documents are integrated in the local working tree; no new release/commit, app code/build/tests, model download, credential change, fleet enrollment or old-node modification. Prior progress, graph stamp and SSD/Yume research preserved. Exit checklist: standalone/canonical-plan/read-only-source/local-check rules satisfied; Pinokio launcher destination/example/regex requirements not applicable because no launcher work occurred.

2026-09-15 owner addition: extended the existing plan/cards for GitHub/original-host
model downloads and offline copy/paste folders, complete companion assets, portable
cache links and identical verification. Executable runtime dependencies remain bundled.
Added location/node membership, generation attribution, status/stats freshness and
address/IP + token API contracts suitable for future standalone GenStudio. Inspected
current Hub contracts read-only at06e893e; recorded source references in the Hub inventory.
Owner phrase about not connecting controllers conflicts with the prior topology;
asked one endpoint-layout question and marked only ST-10.TOPOLOGY pending. No new
gateway, direct-node routing or controller linkage assumed. No application changes,
downloads, live calls, credential changes or existing-node changes.

Owner-addition verification: 20 Markdown files and 151 local links checked; task-card prerequisites still match board; git diff --check clean. Independent review prompted endpoint-neutral wording while topology is pending, correct distinction of current Hub/fleet tokens from a possible new GenStudio scope, and explicit staging of imports outside the selected model library. Applied all refinements. No application tests/build run for these documentation changes. Source references and pre-existing work preserved.

2026-09-15 older-M1 planning addition: read applicable standalone instructions and preserved existing working-tree changes. Checked locked MLX/Metal wheels, native/build target and historical qualification; verified official MLX macOS14.0 minimum. Extended design/PRODUCT/README/execution plan and ST-01/02/08/09/11 for original M1, oldest-qualified Sonoma point release, per-model OS/RAM readiness, last-compatible release preservation and pause/drain/recovery around owner-controlled OS restart. No OS updates, app/runtime changes, inference or model downloads. Documentation validation:20 Markdown files,151 local links, clean git diff --check. Independent packaging evidence review requested; physical compatibility remains unproven.

Independent platform audit recorded in research/rescope-platform-floor-2026-09-15.md: native/FFmpeg/MLX artifact selection targets14.0, but no local proof establishes an exact Sonoma point release for the requested complete model set. Existing M1 OmniVoice evidence does not specify Sonoma. Keep all physical OS/model checkpoints open. Reviewed report and linked it from the canonical design; no runtime or OS changes.


## 2026-09-15 — ST-01 implemented and integrated

Owner clarified annotation 1: preserve GenStudio -> location controller -> joined
nodes, using existing Hub/fleet tokens. No direct node-to-GenStudio connection or new
gateway. This clarification was incorporated into the design and ST-02/ST-10 handoffs.

Implemented ten immutable model/companion manifests, separate runtime input contracts,
HTTPS and offline package import/registration, atomic receipts and explicit full Verify.
Independent reviewers scope_packaging and st01_manifests resolved filesystem durability,
anchored verification, receipt refresh and adopted-vs-reference Voice identity findings.

Local verification: 83 foundation tests passed; the initial runtime-path skip was then
run successfully against the existing read-only runtime (1 passed). Adopted Voice:
640 passed / five warnings. Total 724 passing tests. Real Turbo import verified nine
files / 1,618,590,987 bytes, moved-library reuse and offline tokenizer round trip.
Three real HTTPS companion files verified 89,500 bytes; no model weights downloaded.
Image inputs: 69 compatible distributions, 179 byte-exact notices; 182 Mach-O floors
at or below macOS14/arm64, but 115 relocation issues remain ST-03 work.

Implementation072abf6 and closurec16833c fast-forwarded locally into
implementation/voice-runtime. No remote exists; no PR, hosted compute, publishing,
deployment, credential or existing fleet change. ST-01 DONE; ST-02 READY.
See qualification/st01.md and docs/tasks/BOARD.md. Clean implementation worktree
.worktrees/st-01 retains the current build-side runtime and model proof; disposable
source-hardlink scaffold and superseded exploratory venv were removed. Prior personal
research/progress edits and graph stamp remain preserved outside committed scope.


2026-09-15 owner efficiency direction: inspect existing model files first; report
missing download requirements early and overlap authorized downloads with independent
implementation. Continue Ponytail full (reuse/std-library/minimal scope), use existing
Graphify for code navigation and verify actual source. Do not rebuild graphs or
re-download existing model bytes merely as a routine step.

One-time requested local inventory: all eight FLUX/Voice/Turbo packages have every
required file SHA-256 verified; 23,861,459,508 bytes present before cross-package
companion reuse. Whisper Base and Small weights are absent from the inspected local
Image/Voice/global/pilot caches. After shared-file reuse, unique missing downloads total
628,667,821 bytes in 9 files. No external SSD is mounted,
so this is not an inventory of disconnected SSDs or other fleet machines. No downloads,
imports, source changes or inference were started. Exact sources/files/checksums and
missing URLs: .build/model-availability-2026-09-15.json. The inventory used one hash
per unchanged physical source file and completed its full read in 22.8 seconds.
ST-02 remains READY; this inventory does not mark model inference qualified.

## 2026-09-15 — record reconciliation and verification

Handoff audit (read-only) found the integration branch, board, task cards and canonical
progress disagreeing with Git. Reconciled without changing application behavior:

- `implementation/st-03` (the only unmerged branch; `317267d`, 32 files, +2910/−142) was
  merged into `implementation/voice-runtime` by `cd6ed5d`. The branch's ST-03 card
  (IN_PROGRESS, owner st03_image, start `5358a52`, worktree, evidence) had never reached
  the integration branch, so the branch's card read READY/Unassigned while the board said
  IN_PROGRESS. No task work is stranded on an unmerged branch now.
- 71 pre-existing progress lines were committed (`b46b4cb`) together with two finished
  research handoffs that were untracked and therefore invisible to every worktree and lost
  on a fresh clone: `research/ssd-refresh-handoff.md` and `research/yume-i2v-24gb.md`. The
  Graphify query stamp stays uncommitted as a regenerable cache entry.
- ST-09 was `IN_PROGRESS` on the board and `REVIEW` on its card although `7aa7775` had
  already merged `c0855d4`. The card now records implementation `c0855d4`, integration
  `7aa7775` and DONE. Physical login/logout/reboot remains ST-11.
- `tests/test_image_bundle.py` computed `BUILD_PYTHON` from `ROOT.parents[1]`, resolving to
  the nonexistent `/build/.build/voice-runtime/bin/python3`, so the merged tree
  could not pass. Fixed to `ROOT / '.build/voice-runtime/bin/python3'`.

Previously unrecorded finished work found during the audit: a **real packaged FLUX.2 Klein
1K generation** through the assembled native UI + Hub + Image worker, run by st03_image
after `qualification/st03-image.md` was written and never recorded on the card or board.
Job `af4913b2b52b41b0931f919112daf969`, 1280×720, 4 steps, guidance 1.0, seed 42,
105.943 s, 1,696,066 bytes, SHA-256
`290d2fb8cf0cd927d7d0633412bb157a9ee8675684ed2da7e2e1267c6ce919bd`, peak RSS ~1.03 GiB,
Hub output group finalized, `receipt_acked_at` null. Copied out of the st-03 worktree's
gitignored `.build/` into `qualification/evidence/` and recorded in
`qualification/st03-image-real-inference.md`. Export/receipt acknowledgement,
restart-history checks, retry counts and independent review remain open.

Verification of the integrated tree, local on Apple M4 / 16 GiB / macOS 26.6.2: root
`unittest discover -s tests -p 'test_*.py'` 124 tests OK (1 skipped); retained Hub
regression wrapper passed; adopted Voice `pytest` 639 passed / 2 skipped; `bash native/build.sh`
passed boundary and API contract checks. Root and Hub suites must use
`.build/voice-runtime/bin/python3` (3.12) with `PYTHONPATH=.build/test-deps`; system
`python3` is 3.9.6 and fails on `dict | None` and pytest 9. The historical 107/640 counts
were not reproduced as-is on the merged tree.

Automatic startup status: implemented and integrated. `native/StudioHub.swift` maps
`SMAppService.mainApp.status`, registers/unregisters on an explicit preference change,
opens Login Items when approval is required, and registers during normal setup when
enabled; `native/ShellCore.swift` persists `startAtLogin` with a default of on; the bounded
recovery policy lives in `native/StartupRecovery.swift` with five focused tests. No live
registration was performed, so start-at-login is source/fixture-qualified only and the
physical login/logout/reboot check remains ST-11.

Record-keeping cautions were added to `docs/tasks/BOARD.md`: the checkout is ~54 GB but only
~2.7 MB is tracked; there is no Git remote; `.worktrees/` must not be deleted; and test
counts in receipts are historical. Undo point for this pass: `backup/pre-handoff-7aa7775`.

### 2026-09-15 — GUI smoke and leftover-process cleanup

Launched `dist/Studio Hub 0.4.0 Integrated Proof.app` (bundle `local.studiohub.standalone`).
The window opened, the bundled Hub plus private Voice worker started, and the shell
reported `Local services ready` with the project model library and offline mode on.
Accessibility introspection of the Settings page confirms automatic startup is present
and exposed as a **`Start at login` switch** (`AXCheckBox`, current value 0 = off),
described as "Closing the window keeps local jobs running. Quit stops the app and its
services." The switch was deliberately **not** toggled, so no login item was registered;
`System Events` login items were identical before and after (no Studio Hub entry).
Graceful quit removed `hub-ready.json` and `hub/image-ready.json`; `app.lock` remains.

Pixel screenshots were not available to this session: `screencapture` failed with
`could not create image from display` because Screen Recording permission is not granted
to the agent host. Accessibility (which is granted) was used instead. Grant Screen
Recording to the host if a future session needs real screenshots.

Cleanup finding: the st-03 session had left `ST03 Image Integration.app` plus its
`hub_worker` and `image_worker` running for **3 h 44 min** (PIDs 36844/36862/36891,
parented to the Codex app-server, not to PID 1). They were identified by exact command
line and terminated with SIGTERM; the designed parent-loss cleanup reaped both workers and
no project process remained. The project's exit rule — stop owned processes before
yielding — was not followed by that session.

### 2026-09-15 — ST-03 export/receipt acknowledgement closed; durability added

Durability: the pinned Image runtime was copied out of the disposable
`.worktrees/st-03/.build/` into `.build/st03-image-runtime-worker` and verified against the
recorded tree SHA `c9cd56a6d1cde6b72dee8ba22cf45fcbd640538207038087261cae91ec67f67e`
(content and modes identical). A `git bundle` of all refs was written and copied off-disk
to iCloud Drive, and a bare remote `icloud` was added and pushed (`--all` and `--tags`), so
history is no longer single-disk only. No hosted remote exists; nothing was published.

Artifact: `dist/Studio Hub 0.4.0 Image Proof.app` was staged from the merged tree with
`--image-runtime`, so it bundles `runtimes/image` and `image_worker.py`. Its manifest
records `implementation_commit 86b11d3`, payload
`0cabfe857b9afaef74e22d9cd54dd90c4fbacfb2cd0179d931fcfb0402a6f63a` and the verified Image
runtime tree SHA. Re-staging over the previous bundle first required `chmod -R u+w`; `rm -rf`
alone fails on the read-only bundled runtime and leaves a partial bundle.

Export/receipt acknowledgement, previously the largest open ST-03 gap: exercised end to end
through the Hub with a real generation (batch `d8c27a4e8e`, studio job
`c6ee61743e46479482bbc93a7f31f7be`, 1,696,066 bytes, SHA-256 `5e4cd5db…`, 107.36 s).
`receipt_acked_at` was null before, and `POST /api/hub/jobs/d8c27a4e8e/items/0/ack` returned
`{"ok": true}` and set `receipt_acked_at = 1789482968.542187`. Batch detail is preserved at
`qualification/evidence/st03-ack-proof.json`; the app was then stopped and both readiness
files were removed with no process left behind.

Native wiring `86b11d3`: `native/StudioAPI.swift` `saveImage` now acknowledges delivery only
after the written file is byte-verified, checks the returned `ok`, and reports a rejected
acknowledgement while leaving the file on disk (the managed copy stays protected, so cleanup
never treats an unacknowledged output as delivered). `tests/native/api_main.swift` covers
no-ack-on-unverified-read, ack-exactly-once-on-verified-save and the rejected path;
`bash native/build.sh` passes boundary and API contract checks. The root suite still passes
124 tests (1 skipped) and Hub regression passes.

ST-03.3 is now closed. ST-03.5 still needs restart-history checks and a live GUI **Save**
against a real Hub (the proof above drove the Hub API directly), ST-03.6 retry counts remain,
and independent review is still owed before ST-03 can be DONE. Separately,
`StudioViews.swift` `saveAudio` writes audio with no verified-destination acknowledgement, so
the same wiring is owed for Voice export under ST-08.

### 2026-09-15 — session handover (the wheel changes hands)

The Codex session that had been driving the project ran out of credits mid-flight. It left
the repository with: the Image vertical slice committed but unmerged, the canonical progress
ledger uncommitted, two finished research handoffs untracked, a merged tree whose test suite
was red, a board that contradicted Git, and a Hub + Image worker pair still running.

A DeepSeek Harness session took over the wheel on 2026-09-15 and, in order: committed the
stranded documents; recorded and then merged `implementation/st-03` (`cd6ed5d`); fixed the
Image bundle test path so the suite went green; corrected ST-09 to DONE with its integration
commit; preserved the previously unrecorded real Image generation as tracked evidence;
preserved the pinned Image runtime outside the disposable worktree; added the `icloud` remote
and an off-disk `git bundle`; proved the receipt-acknowledgement contract end to end and wired
it into the native save path (`86b11d3`); and added the `▶ TAKE OVER HERE` block at the top of
`docs/tasks/BOARD.md` so the next session starts from a stated position instead of a stale row.

That block is now the single place to look first: it names the current holder, the integration
commit, what is DONE, the one task in progress, what unlocks next, and the rules that will bite
a newcomer. Undo point for the whole pass: `backup/pre-handoff-7aa7775`.

### 2026-09-15 — owner scope set; ST-04/05/06 dispatched

Owner confirmed the remaining scope: finish the whole app including the fleet/GenStudio
connection. Model-fit checking is the owner's call and must not consume agent time — the
owner has an M1-8, M1-16, M2-8, M2-16, M4-16, M4-24 and M2 Max-32 and will state the RAM a
model needs directly. Distribution is "copy the built app to the Mac and open it with
right-click → Open", so an unsigned build is acceptable and signing/notarization stays out
of scope. Recorded in the board's TAKE OVER HERE block.

Dispatched three workers in isolated worktrees: `st-06` (independent Subtitle backend),
`st-04` (Qwen Base backend) and a read-only independent reviewer for ST-03. The coordinator
owns native/UI and staging integration for all of them, and recorded a deliberate exception
that unlocked ST-04/05/06 before ST-03 formally reached DONE, because the packaged-worker
pattern they depend on is already proven by the working Image slice.

Coordinator discovery while scoping ST-04 — the voice-reference path is HALF-BUILT:
`components/hub/backend/execution_assets.stage_voice_reference()` exists and is unit-tested,
and `broker.py` consumes `voice_reference_asset_id` and streams the staged clip to the Voice
worker's `/api/generate/txt2speech/reference`, but **nothing in production code calls
`stage_voice_reference`** — its only callers are tests. It also requires a GenStudio
`source_asset_id`, and there is no Hub route that stages a reference from a locally chosen
file. Consequence: a user of the standalone app cannot create a voice reference at all
today, so Qwen Base voice cloning cannot work from the app even though the backend supports
references. Folded into the ST-04 worker with a request for the exact wire contract; the
native Audio UI (reference picker + transcript, Base model choices) is coordinator-owned and
follows that contract.

### 2026-09-15 — owner clarification: per-page and unified (tabbed) generation history

Second requirement missed during planning. The owner wants **each generation page to show its
own easy-to-view history** of that studio's generations **and one unified history presented with
tabs**. Recorded in `docs/standalone-plan.md` §2, as checkpoint **ST-08.HISTORY**, and in the
board's TAKE OVER HERE block.

Current state, verified by reading the native shell: Image shows "Recent images" (5) with an
All-Jobs button; Voice shows "Recent audio" (5) likewise; **Subtitle has no history view at all**
and only holds the latest transcription result; the shared **Jobs** page is a flat two-section
scroll (image jobs, then audio jobs) rather than a tabbed unified history. So this requirement is
partly built and partly missing, and it is now explicit rather than assumed.

### 2026-09-15 — ST-04 and ST-06 integrated; native clone UI and tabbed history

Both worker tasks were merged and verified on the integration branch.

`implementation/st-04` (`d24150f`, `4939329`; merged `71611bb` + follow-up): unified
voice-reference staging (local upload and GenStudio push through one contract), Qwen Base clone
requirements in the Voice backend, the Hub envelope lift so a top-level
`voice_reference_asset_id` reaches the item params, and **real offline receipts for both tiers** —
QWEN06 `50f45ef0…` job `4991b1731e6f` and QWEN17 `e7dd0585…` job `195fc324ad7f`, both TER/CER
0.0 on reference and output, 0 retries, 1.97/1.84 GiB peak RSS. Coordinator applied the
native/staging checklist: clone-vs-preset and the transcript requirement are now derived from the
catalog's `reference_audio` claim rather than the model name, and `components/voice/model-audits`
stays in the bundle because the reference contract is audit-bound.

`implementation/st-06` (`9c0f676`; merged `0d792c9`): the independent Subtitle worker, its dispatch
regression and focused tests, and real packaged Turbo transcription evidence. The worker's turn
was stopped and its uncommitted work checkpoint-committed by the coordinator. Its five-step
integration checklist was then applied: `hub_worker.py` starts a `subtitle` child that reuses the
shared Voice runtime with its own state root and an explicit `modality: "transcription"`;
`stage_voice.py` bundles the Subtitle source and worker and records the component with the shipped
Voice runtime identity; `standalone.py` grew a generic `worker_read` helper plus the
`/studio/subtitle/api/*` reads. **Verified live**: a freshly staged app starts three workers —
`voice` / `voice`, `image` / `image`, `subtitle` / **transcription** — and
`/studio/subtitle/api/transcribe/availability` returns 200 from the Subtitle worker.

Integrated evidence: root suite **188 OK (5 skipped)**, Hub regression passed, Voice regression
**664 passed / 2 skipped**, native build passes.

Coordinator decision (from the ST-06 handoff's open question): public Subtitle jobs are served by
the `subtitle` worker, while the Voice worker keeps its Whisper quality check internal to the
parent Voice job and does not advertise `modality == "transcription"` to the packaged public
scheduler. No dual-routing rule was introduced.

Honest limits recorded, not smoothed over: ST-04 and ST-06 are both **PARTIAL**. Whisper Base and
Small are **catalog-only** — `mlx-audio` loads only `*.safetensors` while those pinned revisions
ship `weights.npz`, so the app refuses them with an explicit reason. The Subtitle screen and its
history do not exist in the app yet. Neither task has had independent review.

New finding worth its own attention: `components/model_packages._stamp()` includes `st_ctime_ns`,
so **hard-linking a model-library file changes the stamp**. The ST-04 proof harness builds its
offline cache by hard-linking the shipped library, and the packaged app consequently reported
`File changed; verify again` for Whisper Turbo even though the file hashes to its recorded value
(confirmed directly; the file now carries 4 hard links). The owner remedy is a **Verify** pass; the
harness should copy instead of hard-linking, and whether `ctime` should count as tampering is a
question for ST-11.

### 2026-09-15 — owner clarification: clone-based TTS is the primary voice mechanism

The owner stated a requirement that was missed during the original planning pass: **voice
generation must use voice cloning for consistency**, not preset speakers, and it must work
from **both** a locally chosen reference clip and a clone pushed from GenStudio. This is
recorded now so it cannot be lost again:

- `docs/standalone-plan.md` §2 gained an explicit owner-confirmed requirement for two-way
  voice cloning converging on one Hub reference-asset contract.
- `docs/tasks/ST-04.md` gained checkpoint **ST-04.CLONE**: one reference contract serves a
  locally chosen clip and a GenStudio-pushed clone, same validation, same resulting voice
  identity, and a clone never has to exist as a local file.
- `docs/tasks/ST-10.md` gained checkpoint **ST-10.CLONE**: accept a GenStudio-pushed
  reference (source asset identity, checksum, optional transcript, language, expiry) with no
  local file, and prove the same voice identity as a local clone.
- The board's TAKE OVER HERE block records it as a primary Voice mechanism.
- The `st-04` worker was re-briefed: the ingestion route must not be local-only, and the
  existing GenStudio `source_asset_id` semantics and tests must not be weakened.

This makes the half-built reference path found earlier the centre of the Voice feature rather
than an edge case: today neither origin can stage a reference in production, so clone-based
TTS cannot work from the app at all. Native Audio UI (reference picker + optional transcript,
Base model choices) remains coordinator-owned and will follow the settled wire contract.

## 2026-09-20 — Codex takeover and recoverable development baseline

[MEASURED] Start 415df68 on implementation/voice-runtime; installed app 0.6.38.
The installed bundle started and answered authenticated health. The original source
checkout had 584 tests with 2 failures/2 errors (all Music manifest contract checks),
7 skips, plus a passing 722-test Voice suite and 54-test Hub suite.

[MEASURED] Root preserved 17 unfinished Music files by byte/SHA comparison, committed
them with a recovery card as 0eebc42 on codex/recovered-music-draft, verified the same
commit at icloud, and retained the original in a named stash. The main checkout's ten
catalog tests then passed. The unrelated untracked .serena configuration was preserved.

[MEASURED] Logs exposed job_storage's missing execution_assets import and cancellation
escaping the retry wait. Executable regressions reproduced both. Independent review
identified additional races and containment failures in the newly reactivated purge;
root reproduced them and protected stage/read/delete/purge with one store transaction
lock, skipped invalid deadlines, and refused symlink roots. The production consumers
now copy validated reference bytes before expiry can remove their file. Source is on
codex/takeover-storage-repair in the named Codex repair worktree.

Final gate, packaged proof and integration state are recorded in
qualification/takeover-2026-09-20.md and docs/tasks/BOARD.md. This is recovery and a
maintenance repair, not new Music completion or physical hardware qualification.

[MEASURED] Final source/test commit: 5892089. Fresh final root suite: 584 tests OK
(7 skipped, 427.715 s); nested Voice 722 passed/19 skipped; retained Hub 54 passed.
Native build passed. The final development bundle passed seven authenticated endpoints,
401 unauthenticated refusal, three studio modalities, scheduled temporary-asset expiry,
live-copy preservation and owned shutdown. Evidence is tracked under
qualification/evidence/takeover-2026-09-20/. The local installed app remains unchanged.

## 2026-09-20 — portable continuation audit

[MEASURED] Owner requested possible bugs/fixes, a new-computer handoff and GitHub
synchronization. Started `f6362e0`, branch `codex/new-computer-handoff`; root owns
docs/evidence/board, Luna workers supplied bounded read-only inventories and review.
`docs/NEW-COMPUTER-HANDOFF.md` is the new entry point;
`qualification/bug-audit-2026-09-20.md` records five reproduced failures plus scoped
static candidates and migration blockers. Product source remains unchanged.

[MEASURED] `codex/recovered-music-draft` at `0eebc42` is now on GitHub; every prior
local branch tip was compared with origin and matched. Recovery worktrees, stash,
installed app and external model/runtime inputs remain preserved. Private repo:
`theng12/Studio-Hub-KH`, default `implementation/voice-runtime`. No Actions workflows
exist; none were run. Repo settings otherwise preserved.

[MEASURED] Offline audit probes return five expected assertion failures on the
audited source; raw log and script are retained under
`qualification/evidence/handoff-2026-09-20/`. Review identified import-time registry
directory creation; the probe now sets temporary STUDIOHUB_DATA_DIR before imports.
Current lockfiles match runtime lock receipts; approximate asset sizes and exact
manifest/ref checksums are recorded. New-machine hardware, runtime reconstruction,
model inference and physical fleet/reboot acceptance remain unmeasured.

The current full test/build baseline is the prior takeover receipt; no product
code changed to justify repeating that full gate. Finish this handoff through
independent documentation/evidence review, local validation and authorized PR integration.

[MEASURED] Handoff content committed as `c4909a6`; independent content-review
corrections resolved. Local validation: 55 links, 11 shell blocks, Python syntax,
and the expected five negative-probe failures. Integration record:
https://github.com/theng12/Studio-Hub-KH/pull/2 (use its merge state/commit).

## 2026-09-21 — resumed Music runtime and new-computer verification

[MEASURED] Integration was clean at `9f25c03`, containing reviewed H-01 through
H-05 and Music package-contract PR #10. Root resumed the preserved
`codex/music-package-runtime` worktree, without merging the old draft wholesale.
The runtime segment stages 618 exact-revision source files and a seven-package
hash-locked overlay over Voice Python, supervises a private Music worker, adds
closed proxy routes, and retains uncertain accepted jobs for exact-job recovery.

[MEASURED] Native build and SDK 26.5 selection passed. The existing Image runtime
matched the tracked lock and passed manifest verification before and after a
separate project-owned copy. Product runtimes were not modified. No live state,
credentials, model library or installed app was copied or changed.

[REPORTED] Final independent review found no remaining findings after regression
repairs for normalized accepted parameters and terminal-persistence rollback.
It passed 85 Hub, 46 Music/package/baseline (four skips), 37 auth/peer tests and
three authenticated HTTP recovery fault injections. Root owns the full gate and
packaged evidence, recorded in `qualification/new-computer-resume-2026-09-21.md`.

[MEASURED] The real pinned phonemizer works through its bundled shared library
without a host executable. Tests now exercise that path, explicitly select a
staged app and verify its source commit. A missing package marker was restored so
ordinary unittest discovery actually invokes the isolated Hub regression suite.
Graphify CLI/interpreter are unavailable and its old graph has no relevant Music
coverage; current callers were inspected directly.

Next: native lifecycle reproductions N-01/N-02/N-03, bounded reference-validation
repairs C-01/C-02, then the native Music workflow and separate model qualification.
Physical machine/fleet/GenStudio checks remain open. No deployment, hosted compute,
paid calls, releases, tags, notarization or real enrollment occurred.

[MEASURED] Final product commit `c9eadca` passed independent exact-commit review.
Root: 631 tests OK/7 skips; Voice: 722/19; Hub: 85. Clean-commit app
`.build/StudioHubReviewed.app` passed 21 release/phonemizer checks and the
model-free four-worker startup/authentication/identity/owned-shutdown probe.
Exact tree digest and source hashes are in the resume receipt and sanitized JSON.
PR #11 records integration: https://github.com/theng12/Studio-Hub-KH/pull/11.

## 2026-09-21 — R04 node-owned peer admission (DeepSeek takeover)

[MEASURED] Base `9a4570da57cb9109cd7da1d06d2197f923a71139` (PR #13 merged). The
peer proxy forwarded work-submitting POSTs to local workers without any
node-owned admission. Reproduced all four bypasses on the base: model pause,
Studio disable, output-storage refusal and a reserved physical machine each
returned HTTP 200 and one worker generation request.

[MEASURED] Repair gated only work-submitting POSTs (`/api/generate/...`,
`/api/transcribe`) through node-owned checks - machine/Studio disable, paused
model, physical-machine lease, output admission and packaged readiness - and
returned machine-readable 409/507/503 refusals. Status, cancel, artifact and
acknowledgement paths stay reachable for accepted work. Accepted asynchronous
peer work now holds a machine lease until observed terminal (safety TTL plus a
60-second sweep). After repair the probe reports 409/409/507/409 with zero
worker requests.

[MEASURED] `tests/test_studio_proxy.py` 14 passed; `tests/hub_regression/cases`
108 passed; `test_hub_jobs.py` + `test_hub_dispatch_proof_gate.py` +
`test_hub_standalone.py` 18 passed; `test_standalone_connections.py` 32 passed
with one pre-existing environmental failure
(`test_the_overview_shows_every_machine_in_one_row`: `apple_chip_name()` runs
`sysctl` by name and this harness's PATH omits `/usr/sbin`; the test passes with
`/usr/sbin` on PATH and failed identically before the repair). Original five
handoff probes OK. A first post-repair `test_hub_jobs` run exposed a
`CancelledError` from the new lease-sweep task that did not swallow
cancellation; fixed by mirroring `job_storage.budget_loop`.

Self-review only; independent review of R04 remains pending.

## 2026-09-21 — host identity independent of PATH (DeepSeek)

[MEASURED] `test_the_overview_shows_every_machine_in_one_row` failed on this
harness because `resources.apple_chip_name()` ran `sysctl` by name and the
harness PATH omits `/usr/sbin`; the fleet overview reported no chip. The same
test passed with `/usr/sbin` on PATH. The defect is real for a launchd-started
packaged app, which does not inherit a login shell's PATH.

[MEASURED] `apple_chip_name()` now tries `/usr/sbin/sysctl` first, then the bare
name, then `system_profiler`'s `chip_type` (some Apple Silicon builds report an
empty `machdep.cpu.brand_string`). `apple_machine_type()` uses the same absolute
path preference. New `tests/test_hardware_identity.py` proves both with
`PATH=/nonexistent`.

[MEASURED] `tests/test_hardware_identity.py` 2 passed;
`tests/test_standalone_connections.py` full **33 passed** (previously 32 passed,
1 failed). `test_hub_statistics.py` + `test_hub_standalone.py` +
`test_hub_process.py` + `test_fleet_credentials.py` + `test_hardware_identity.py`
+ `test_studio_proxy.py` 40 passed with 122 subtests.

## 2026-09-21 — R05 records reconciliation (DeepSeek)

[MEASURED] Reconciled ST-03/04/05/06/08/10/11 against current source, focused
tests and receipts in `r05-report.md`. ST-06 (independent Subtitle) is
implemented locally but its card still read WAITING/0-checked; ST-04/ST-05 code
is present with real-model receipts gated. Labeled implementation versus missing
evidence versus hardware gates; deliberately did not bulk-tick checkboxes.
Corrected the ST-04/ST-05/ST-06 status lines.

[MEASURED] Recorded the separate test-dependency setup (`.build/test-deps`,
exact versions) and confirmed the Image offline-build inputs remain undeclared
and were not attempted (may need network). Graphify CLI remains unavailable; the
saved graph predates current Hub/native/Music work and callers were inspected
directly.

## 2026-09-21 — R04 multipart coverage and full root baseline (DeepSeek)

[MEASURED] Added a Subtitle multipart admission test (the `model` form field
path) and a subtitle worker to the proxy fixture; `tests/test_studio_proxy.py`
now 15 passed / 6 subtests.

[MEASURED] Full root suite on the current tree, without a staged app selected:
`.build/voice-runtime/bin/python3 -B -m unittest discover -s tests -p 'test_*.py'`
→ **651 tests, OK, 11 skipped, 443.295s**. The four extra skips versus the
recorded 7 are the app-dependent release/bundled checks, because this run did
not set `STUDIOHUB_TEST_APP`; the historical 641/7 run selected the preserved
`936b3e5` app. ResourceWarnings from fixture sockets remain visible.

## 2026-09-21 — R05 Image offline-rebuild check (DeepSeek)

[MEASURED] Declared inputs verified present: CPython `3.12.14+20260901` archive
(`.build/downloads`) with matching sha; `packaging/image-runtime/requirements.lock.txt`
sha `5fd5e79…`; pins in `packaging/voice-runtime/pins.env`. The existing
`.build/st03-image-runtime-worker` still verifies.

[MEASURED] Offline rebuild attempted with an empty user cache (`env -i HOME=<temp>`)
and only project inputs (`UV_CACHE_DIR=.build/uv-cache`). It failed **loudly** at
`uv pip sync --offline`: `contourpy==1.4.0` was not in the cache and the network
was disabled. No silent network use; no partial runtime left.

[MEASURED] The project uv cache is an incomplete Image input: 21 of 73 locked
packages have no registry metadata there, including `mflux`, `matplotlib`,
`opencv-python`, `contourpy`, `kiwisolver`, `fonttools`, `hf-transfer`, `keyring`
and `twine`. Proving offline reconstruction therefore needs an explicitly
populated, declared Image wheel cache (a network fetch treated as separate
authorization). This is a bootstrap/kit gate, not an app defect. Disposable temp
home and any stage directory removed.

## 2026-09-21 — R09 selected-device no-fanout proof (DeepSeek)

[MEASURED] Added Hub-side selection tests to `tests/test_memory_policy_contract.py`:
`set_mode('memory_saver', ['voice'])` reaches only the selected Voice Studio and
leaves the Subtitle sibling's mode and request log untouched; `release(['voice'])`
frees only the selected Studio and leaves the sibling's loaded models intact. Suite
now **16 passed** (was 14). This closes the "no unselected fanout" half of ST-10.4;
durable acknowledgement and two-machine deduplication remain unproven.

## 2026-09-21 — shared-voice flake re-probe (DeepSeek)

[MEASURED] Ran the shared-voice peer test
`test_a_voice_clone_submitted_to_the_controller_runs_on_the_node` three times in
isolation: **3 passed** (23.76s, 23.77s, 11.70s). The documented earlier
shared-voice peer reachability failure still does not reproduce.
**Not declared fixed**; the cause remains unproven.

## 2026-09-21 — R04 two-Hub storage refusal (DeepSeek)

[MEASURED] Added `test_a_node_refuses_peer_work_when_its_output_storage_is_full`
to `tests/test_standalone_connections.py`: two real Hubs joined, the node's owner
sets `minimum_free_bytes` out of range, and a fleet POST to the node's peer route
is refused `507 OUTPUT_STORAGE_BLOCKED` with no worker submission. Passed (3.13s).
R04 now has two-real-Hub proofs for Studio-disable and output-storage; model
pause and physical-machine reservation remain proven at the peer HTTP route only.

[MEASURED] Full `tests/test_standalone_connections.py` with both new two-Hub
tests: **34 passed**, 347.11s. Earlier count was 32 passed / 1 failed (the
PATH-caused chip failure, since fixed).

## 2026-09-21 — R06 native Music UI render (independent pre-integration check)

[MEASURED] Rendered the in-progress Music workspace offscreen at minimum and
normal sizes:
`STUDIOHUB_MACOS_SDK=macosx26.5 STUDIOHUB_RENDER_LIVE=0 bash native/render-page.sh music .build/deepseek-music-render 1040x680 1920x1053`
→ exit 0, two PNGs. Both show a real workflow (prompt editor, character count,
Generate Music, Generation/Model picker, Manage Music Models, Preview pane,
Recent music/All Jobs) and the "Start your local services" block — **not** the
former "Engine not installed" placeholder. Renders inspected directly.
Also ran the native test binaries built from the same sources:
`native-boundary-tests` / `native-api-tests` / `native-lifecycle-tests` all passed,
and `tests/test_generation_time.py` + `test_audio_layout.py` +
`test_native_build_scripts.py` + `test_peer_path_contract.py` → 42 passed.
Not yet integrated; the subagent is still finalizing.

## 2026-09-21 — R06 native Music workflow (DeepSeek + bounded subagent)

[MEASURED] Implemented the native Music workflow that was missing behind the
"Engine not installed" placeholder. `StudioAPI.swift` decodes the worker's
`generation_defaults`/`generation_limits`, keeps per-studio Music history and a
persisted pending request, and submits through the existing durable
`POST /api/hub/jobs` queue with `modality: "music"`. Artifact retrieval verifies
byte count, SHA-256 and RIFF/WAVE before play/save; delivery is acknowledged only
after a verified save, a rejected ACK leaves the file on disk, and an uncertain
request is never resubmitted as new work. `StudioViews.swift` wires a Music
workspace (prompt, Generate, model/controls, Preview, Save Audio, history,
Cancel/Check Original Result). `tests/native/api_main.swift` adds Music fixtures
and cases; `tests/test_generation_time.py` now expects four job-row styles.

[MEASURED] Integration gate: `STUDIOHUB_MACOS_SDK=macosx26.5 bash native/build.sh`
**exit 0** (boundary, API, lifecycle checks passed; optimized app built). Offscreen
render of the Music page at 1040×680 and 1920×1053 exit 0 and inspected. The three
native test binaries and `test_generation_time.py` + `test_audio_layout.py` +
`test_native_build_scripts.py` + `test_peer_path_contract.py` (42) passed. Full
root suite: **655 tests OK, 11 skipped, 440.729s** (no staged app selected).

[MEASURED] The implementation came from a bounded background subagent under an
explicit non-committing brief; the coordinator reviewed the entire diff (including
the corrupt-artifact/no-ACK and uncertain-request cases), ran the gate, and owns
this record. Independent review of R06 is **pending**. No real Music model ran;
GUI save-panel/keyboard interaction was not driven. Real generation remains R07.
