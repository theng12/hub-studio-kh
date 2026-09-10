# Voice runtime redistribution license inventory

Date: 2026-09-10. This is technical redistribution evidence for the bounded runtime candidate, not legal advice or release approval.

## Collected evidence

`manifest/python-distributions.json` records every installed distribution's version, declared license expression, legacy license field, license classifiers, project URL, VCS direct URL, and declared license-file paths. The build copies every declared license file it can locate to `licenses/python/<distribution>-<version>/` and records its SHA-256. For the current bounded lock, all declared Python license files were found. CPython, FFmpeg, the Open JTalk dictionary and the included HTS voice have explicit top-level files in `licenses/`.

The authoritative review input is the generated inventory plus those copied texts. A metadata label is not a substitute for reading the actual license when distributing the product.

## Key components

| Component | Recorded terms | Evidence and action |
| --- | --- | --- |
| CPython 3.12.14 | PSF license stack | `licenses/CPython-LICENSE.txt`; include with distribution |
| FFmpeg 9.0.1 | LGPL 2.1 or later for this default, non-GPL configuration | `licenses/FFmpeg-LICENSE.md`; retain source offer/compliance materials required by the applicable LGPL terms. FFmpeg explains that enabling optional GPL parts changes the result: [FFmpeg legal page](https://ffmpeg.org/legal.html) |
| pyopenjtalk 0.4.1 | wrapper MIT; Open JTalk Modified BSD; included HTS voice has its own text | Copied package notices plus explicit dictionary/voice files. The project documents the split: [pyopenjtalk PyPI license section](https://pypi.org/project/pyopenjtalk/) |
| mlx-audio | MIT package code at exact VCS revision | copied installed license and `direct_url.json` provenance |
| Unidecode 1.4.0 | GPL v2 or later | This is an explicit source requirement. Redistribution must meet GPL obligations or the package must be removed with engine/caller proof. [upstream repository](https://github.com/avian2/unidecode) |
| soxr 1.1.0 | LGPL 2.1 or later | Preserve license and satisfy dynamic-library redistribution obligations |
| espeakng-loader 0.2.4 | package metadata declares no license and the wheel contains eSpeak NG 1.52 dylibs/data | **Unresolved for release.** The loader wheel supplies no license file or project URL. eSpeak NG upstream describes the engine as GPL v3 or later. Do not ship this wheel until the exact bundled content, notices, corresponding-source obligation and product compatibility have been reviewed. [eSpeak NG upstream](https://github.com/espeak-ng/espeak-ng), [eSpeak NG 1.52 release](https://github.com/espeak-ng/espeak-ng/releases/tag/1.52.0) |

The retained package set also contains common MIT, BSD, Apache-2.0, ISC, MPL and Python-license components. Exact texts and version-level declarations are in the generated inventory rather than summarized into a potentially misleading single license.

## Removed F5 dependency surface

The full adopted generation requirements resolve `f5-tts` and a large dedicated dependency tree. That tree is not part of the bounded Qwen/Whisper artifact because TorchCodec's macOS binaries require unbundled shared FFmpeg ABI libraries. Removal also avoids silently shipping EnCodec 0.1.1 under the `CC BY-NC 4.0` text actually present in that wheel and several additional copyleft dependencies. The current upstream EnCodec repository now describes its code as MIT, but that does not override the license file inside the resolved 0.1.1 artifact. Any future F5 artifact needs its own exact dependency and license qualification.

F5 model weights are separate from code. The Voice source itself labels the SWivid/F5-TTS path non-commercial. No F5 package or model is authorized by this pilot.

## Models and media

No model weights are bundled with the runtime. Each imported Qwen, Whisper, or later model package needs a separately recorded repository, immutable revision, file manifest, license, acceptable-use review and redistribution decision. A code package's license does not establish a model or training-data license.

FFmpeg's codec implementation license does not resolve patent or media-content rights. The candidate disables third-party autodetection and GPL configuration options; release review must retain the exact configure string and source archive used for the shipped binary.

## Release blockers

The current runtime is a technical feasibility artifact, not redistribution-ready, because:

1. `espeakng-loader==0.2.4` omits license metadata/notices while bundling GPL-covered eSpeak NG binaries and data.
2. GPL/LGPL obligations for Unidecode, eSpeak NG, soxr and FFmpeg require a product-level compliance decision and delivery materials.
3. Model licenses remain outside this runtime inventory.
4. Physical clean Sonoma qualification and final signed/notarized app review remain outstanding.

These findings should remain visible even when Qwen and Whisper execute successfully. Runtime behavior does not cure a redistribution-license gap.
