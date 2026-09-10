---
type: "query"
date: "2026-09-10T14:15:58.204488+00:00"
question: "Which existing runtime, storage and fleet assumptions must change for a standalone Studio app on new nodes?"
contributor: "graphify"
outcome: "corrected"
correction: "Confirmed against Voice commit 154e7390fcee942ca6bbbcd326fcf9027b37404d. Current locations include auto_update.py _pinokio_home line 480, settings.py _PATH line 20, voices.py VOICES_DIR line 34. Graph is not a Hub or Image inventory."
---

# Q: Which existing runtime, storage and fleet assumptions must change for a standalone Studio app on new nodes?

## Answer

Selected vocabulary: dependency runtime service storage fleet capabilities update pinokio; then generation storage cache settings. The copied graph covers Voice only and guided direct source checks. Voice auto_update.py depends on conda_env, pterm and Git; settings.py and voices.py write inside source directories. Standalone packaging needs owned runtime paths, external writable data and package-aware lifecycle. Query truncation and stale line numbers preclude treating the graph as a complete inventory.

## Outcome

- Signal: corrected
- Correction: Confirmed against Voice commit 154e7390fcee942ca6bbbcd326fcf9027b37404d. Current locations include auto_update.py _pinokio_home line 480, settings.py _PATH line 20, voices.py VOICES_DIR line 34. Graph is not a Hub or Image inventory.