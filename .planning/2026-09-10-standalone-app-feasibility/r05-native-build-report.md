# R05 native-build portability slice

Base: `b5506178cd0543a4734911c4fdb3c91d21ad271b`  
Scope: partial R05 only — SDK selection and native lifecycle build gate. This
does not qualify the combined R05 bootstrap/portability work.

## Changed files

- `native/sdk.sh` — shared explicit SDK resolver. The known-good default is
  `macosx26.5`; a defined `STUDIOHUB_MACOS_SDK` is passed unchanged to `xcrun`.
  Empty or unavailable requests fail before compilation and name the override.
- `native/build.sh` — runs the existing lifecycle harness after boundary/API
  gates with `-D LIFECYCLE_TESTING -parse-as-library`, before the optimized app
  build.
- `native/render-page.sh` — sources the same SDK resolver.
- `tests/test_native_build_scripts.py` — disposable fake-`xcrun` behavioral
  tests. Fixtures copy only the native scripts/Swift inputs into a temporary
  project and never write checkout `.build` products.

## RED

Before the script changes:

```sh
python3 -m unittest tests/test_native_build_scripts.py -v
```

Exit 1: 5 expected behavioral failures. The fixed selector ignored explicit
values, the lifecycle executable was not built/run, lifecycle failure could not
stop the build, and unavailable explicit selectors had no required guidance.

## GREEN

```sh
python3 -m unittest tests/test_native_build_scripts.py -v
bash -n native/build.sh native/render-page.sh native/sdk.sh
git diff --check
```

Exit 0. The focused suite has 8 passing tests: build/render default and explicit
selection, missing-SDK refusal before compile, empty override refusal without
`xcrun`, lifecycle compiler flags/execution, and lifecycle failure propagation.

```sh
STUDIOHUB_MACOS_SDK=macosx26.5 bash native/build.sh
```

Exit 0, with:

```text
Native boundary checks passed
Native API contract checks passed
Native lifecycle checks passed
```

```sh
STUDIOHUB_MACOS_SDK=macosx-does-not-exist bash native/build.sh
```

Exit 1 before compilation. `xcrun` rejected the selector and the resolver
reported: `Requested macOS SDK "macosx-does-not-exist" is unavailable. Set
STUDIOHUB_MACOS_SDK to an installed xcrun SDK selector (for example macosx26.5).`

## Limits / handoff

- The explicit native build was run while unrelated integration work was present
  in the shared worktree. A literal `native/StudioViews.swift` copy update was
  announced during this slice, so the coordinator must run the final combined
  native build/render after workers freeze.
- Root owns combined regression/build review. No installed app, package, service,
  model data, deployment, commit, or Git state was changed by this slice.
