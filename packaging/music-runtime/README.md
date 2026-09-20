# Music runtime overlay

ACE-Step shares Studio Hub's pinned Voice runtime for Python, Torch, MLX,
Transformers, Diffusers, FastAPI, and the other heavy inference dependencies.
This builder adds only the seven pure-Python packages missing from that base.
All versions and hashes come from ACE-Step revision
`ca1e85fe9430179831e6bc6be790c332190a3866`.

Build after the Voice runtime and pinned ACE-Step source checkout exist:

```sh
packaging/music-runtime/build.sh \
  .build/music-runtime \
  .build/music-engine-source \
  .build/voice-runtime/bin/python3.12
```

The output is a read-only `sys.path` overlay, not a second Python environment.
The build smoke imports ACE-Step's handler, LM, and inference entry points
without loading model weights. The accepted output tree digest is pinned in
`packaging/stage_voice.py`; staging and packaged worker startup both reject any
different tree.
