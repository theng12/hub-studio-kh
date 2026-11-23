#!/bin/bash
# ST-14.8 review (2026-09-25) — what the SHIPPED 0.6.62 bundle actually publishes.
#
# Boots the packaged Hub from the built 0.6.62 bundle (a snapshot, not the working tree) with
# the real model library, and reads the routes the native app reads. No model is loaded: no
# completion is requested, so this costs seconds and does not touch the 6 GB snapshot.
set -u
ROOT="$HOME/Developer/_orchestra/developer-media/hub-studio-kh/app"
APP="$ROOT/.build/StudioHub-0662/Hub Studio KH.app"
RES="$APP/Contents/Resources"
PY="$RES/runtimes/voice/bin/python3"
OUT="$ROOT/.build/chat-review-2026-09-25"
DATA="$(mktemp -d /tmp/chat-review.XXXXXX)"
# An EMPTY throwaway library: never the real ~/StudioHubModels (a Hub with a throwaway data dir
# shelves/retires whatever library it is given into that data dir; see qualification/library_guard.py).
LIB="$DATA/empty-library"; mkdir -p "$LIB/.studiohub"; chmod 700 "$LIB/.studiohub"
READY="$DATA/hub-ready.json"
mkdir -p "$OUT"; chmod 700 "$DATA"

echo "=== bundle under test ==="
python3 - "$RES/release-manifest.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("version", d.get("version"), "commit", d.get("implementation_commit"),
      "development_build", d.get("development_build"))
print("components", sorted(d.get("components", {})))
PY

echo
echo "=== starting the packaged Hub (starts all packaged workers as children) ==="
"$PY" -I -B "$RES/hub_worker.py" --data-dir "$DATA" --models-dir "$LIB" \
  --ready-file "$READY" --port 0 --host 127.0.0.1 --offline >"$OUT/hub.out" 2>&1 &
HPID=$!
for _ in $(seq 1 240); do [ -f "$READY" ] && break; kill -0 "$HPID" 2>/dev/null || break; sleep 1; done
if [ ! -f "$READY" ]; then echo "FAIL: Hub never became ready"; tail -30 "$OUT/hub.out"; kill "$HPID" 2>/dev/null; exit 1; fi
URL="$(python3 -c "import json;print(json.load(open('$READY'))['url'])")"
TOKEN="$(python3 -c "import json;print(json.load(open('$READY'))['token'])")"
echo "hub ready: $URL"

echo
echo "=== /api/health workers ==="
curl -s --max-time 30 -H "Authorization: Bearer $TOKEN" "$URL/api/health" >"$OUT/health.json"
python3 - "$OUT/health.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("ok:", d.get("ok"), "app_version:", d.get("app_version"))
for w in d.get("workers") or []:
    print("  worker", w.get("id"), "modality", w.get("modality"), "state", w.get("state"),
          "studio_version", w.get("studio_version"))
PY

echo
echo "=== /api/hub/model-packages (the route native refresh() reads with a hard try) ==="
curl -s --max-time 60 -o "$OUT/model-packages.json" -w 'HTTP %{http_code}\n' \
  -H "Authorization: Bearer $TOKEN" "$URL/api/hub/model-packages"
python3 - "$OUT/model-packages.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
rows = d.get("models") or d.get("packages") or []
print("total rows:", len(rows))
chat = [m for m in rows if m.get("worker_id") == "chat" or m.get("modality") == "chat"
        or m.get("engine") == "chat-mlx"]
print("chat rows:", len(chat))
for m in chat:
    print(json.dumps({k: m.get(k) for k in
        ("package_id", "model_id", "engine", "modality", "worker_id", "capabilities",
         "files_state", "runtime_state", "worker_state", "dispatch_state", "reason")},
        indent=1, default=str))
PY

echo
echo "=== aggregate catalog chat rows (every worker's /api/catalog, folded) ==="
curl -s --max-time 60 -o "$OUT/catalog.json" -w 'HTTP %{http_code}\n' \
  -H "Authorization: Bearer $TOKEN" "$URL/api/hub/catalog"
python3 - "$OUT/catalog.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
rows = d.get("models") if isinstance(d, dict) else d
if not isinstance(rows, list):
    print("catalog shape:", json.dumps(d)[:400]); raise SystemExit
print("total rows:", len(rows))
chat = [m for m in rows if m.get("modality") == "chat" or m.get("engine") == "chat-mlx"
        or "chat.completion" in (m.get("capabilities") or [])]
print("chat rows:", len(chat))
for m in chat:
    print(json.dumps({k: m.get(k) for k in
        ("model_id", "repo", "label", "engine", "modality", "capabilities", "available",
         "context_window_tokens", "max_output_tokens")}, indent=1, default=str))
PY

echo
echo "=== worker /api/catalog from the packaged chat worker itself ==="
CHAT_READY="$(ls "$DATA"/hub/chat-ready.json 2>/dev/null)"
if [ -n "$CHAT_READY" ]; then
  CURL="$(python3 -c "import json;print(json.load(open('$CHAT_READY'))['url'])")"
  CTOKEN="$(python3 -c "import json;print(json.load(open('$CHAT_READY')).get('token') or '')")"
  echo "chat worker: $CURL"
  curl -s --max-time 30 -H "Authorization: Bearer $TOKEN" -H "X-Studio-Token: $CTOKEN" \
    "$CURL/api/catalog" >"$OUT/chat-catalog.json"
  python3 - "$OUT/chat-catalog.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:
    print("unreadable:", exc); raise SystemExit
for m in d.get("models") or []:
    print(json.dumps({k: m.get(k) for k in
        ("label", "repo", "modality", "capabilities", "available", "execution_ready",
         "context_window_tokens", "max_output_tokens", "qualified_minimum_memory_gb",
         "memory_eligible", "cache")}, indent=1, default=str))
    print("model_limits:", json.dumps(m.get("model_limits"), indent=1))
PY
else
  echo "no chat ready file found under $DATA/hub"
fi

echo
echo "=== the app's own gate: does the model library carry the install receipt? ==="
# Read-only look at a receipt index: set REAL_INDEX to inspect a real one (the Hub itself never gets it).
python3 - "${REAL_INDEX:-$LIB/.studiohub/installed.json}" <<'PY'
import json, sys, os, time
path = sys.argv[1]
d = json.load(open(path))
print("index entries:", len(d), "chat entry:", "qwen3-5-9b-4bit" in d,
      "mtime:", time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))))
PY

echo
echo "=== shutting down ==="
kill "$HPID" 2>/dev/null
for _ in $(seq 1 60); do kill -0 "$HPID" 2>/dev/null || break; sleep 1; done
kill -9 "$HPID" 2>/dev/null
pkill -f "runtimes/voice/bin/python3 -I -B $RES" 2>/dev/null
left="$(pgrep -f "runtimes/voice/bin/python3 -I -B $RES" | wc -l | tr -d ' ')"
echo "children remaining: $left"
echo "artefacts: $OUT"
