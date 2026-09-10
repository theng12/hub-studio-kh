# Findings

## Evidence already established
- Voice SDK fix is merged as 2.7.2; it handles native-build SDK mismatch, not general packaging compatibility.
- MLX 0.31.2 and mlx-metal 0.31.2 publish separate macOS 14/15/26 arm64 wheels. Current installed wheels target 26; packaging must explicitly select the intended target builds.
- MLX documents macOS 14+ Apple Silicon support; full model/engine qualification on 14 remains unproven.
- Existing SSD bootstrap installs generation environments; it is not a standalone relocatable application.
- GenStudio consumes authenticated Hub API and immutable release/runtime identities. Git/Pinokio maintenance assumptions require explicit packaged-runtime adaptation.
- Existing Graphify graph at /build/pinokio/graphify-out/graph.json has 747 nodes, 1669 edges and 27 source-file entries, covering Voice backend only. It is a navigation aid, not current proof of runtime reachability. No Hub graph was found.

## Sources
https://ml-explore.github.io/mlx/build/html/install.html
https://pypi.org/pypi/mlx/0.31.2/json
https://pypi.org/pypi/mlx-metal/0.31.2/json
https://conda.github.io/conda-pack/
https://pyinstaller.org/en/stable/usage.html

## Source inspection
Voice auto_update.py fixes interpreter to conda_env/bin/python, resolves Pinokio home and invokes pterm; main.py has source-relative writable paths and Pinokio UI port. Wrapping existing folders is insufficient; standalone-owned source adapters must separate app resources, node data, process lifecycle and package updates. Missing native_execution.py search path was harmless; discovered module names will be used instead.

## Final planning conclusions
- Source-linked independent Hub inventory is saved in research/hub-contract-inventory.md; primary review confirmed its release identity and role constraints directly.
- Fixed upstream repository identities and Git-derived runtime hashes are a concrete packaged release integration issue. Manual pilot updates cannot be called full managed-release support.
- Thin app shell, isolated runtimes, external writable state/model storage, and Voice-first qualification are proposed. No app implementation or hardware qualification occurred.
- First scope defaults to Hub + Image + Voice after an optional scope question received no reply during drafting; owner can revise it.
- Apple direct-distribution and SMAppService documentation support the proposed later distribution/login workflow; no signing submission or login service registration occurred.
- Missing guessed child CAPABILITY_CONTRACT.md files were resolved through actual Hub contract/child README paths; no mutation or lost work.
