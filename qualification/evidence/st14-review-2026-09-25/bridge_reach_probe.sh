#!/bin/bash
# ST-14.8 review — is the chat lane reachable on the Hub surface the gateway-kh adapter uses?
# GETs only: no model is loaded.
set -u
ROOT="$HOME/Developer/_orchestra/developer-media/hub-studio-kh/app"
RES="$ROOT/.build/StudioHub-0662/Hub Studio KH.app/Contents/Resources"
PY="$RES/runtimes/voice/bin/python3"
OUT="$ROOT/.build/chat-review-2026-09-25"
DATA="$(mktemp -d /tmp/chat-bridge.XXXXXX)"; chmod 700 "$DATA"
# An EMPTY throwaway library: never the real ~/StudioHubModels (a Hub with a throwaway data dir
# shelves/retires whatever library it is given into that data dir; see qualification/library_guard.py).
LIB="$DATA/empty-library"; mkdir -p "$LIB/.studiohub"; chmod 700 "$LIB/.studiohub"
READY="$DATA/hub-ready.json"
"$PY" -I -B "$RES/hub_worker.py" --data-dir "$DATA" --models-dir "$LIB" \
  --ready-file "$READY" --port 0 --host 127.0.0.1 --offline >"$OUT/bridge-hub.out" 2>&1 &
HPID=$!
for _ in $(seq 1 240); do [ -f "$READY" ] && break; kill -0 "$HPID" 2>/dev/null || break; sleep 1; done
[ -f "$READY" ] || { echo "FAIL: hub never became ready"; tail -20 "$OUT/bridge-hub.out"; kill "$HPID"; exit 1; }
URL="$(python3 -c "import json;print(json.load(open('$READY'))['url'])")"
TOKEN="$(python3 -c "import json;print(json.load(open('$READY'))['token'])")"
echo "hub $URL"

echo
echo "--- POST /studio/chat/v1/chat/completions with NO credential (must be 401, not 200) ---"
curl -s -o /dev/null -w 'HTTP %{http_code}\n' --max-time 20 \
  -X POST "$URL/studio/chat/v1/chat/completions" -H 'Content-Type: application/json' -d '{}'

echo "--- GET /studio/chat/api/catalog with the Hub token (the gateway's local credential) ---"
curl -s --max-time 30 -H "Authorization: Bearer $TOKEN" "$URL/studio/chat/api/catalog" \
  -o "$OUT/bridge-chat-catalog.json" -w 'HTTP %{http_code}\n'
python3 - "$OUT/bridge-chat-catalog.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:
    print("unreadable:", exc); raise SystemExit
for m in d.get("models") or []:
    print("label:", m.get("label"), "| capabilities:", m.get("capabilities"),
          "| context:", m.get("context_window_tokens"))
PY

echo "--- GET the same with NO credential (must be 401) ---"
curl -s -o /dev/null -w 'HTTP %{http_code}\n' --max-time 20 "$URL/studio/chat/api/catalog"

kill "$HPID" 2>/dev/null
for _ in $(seq 1 60); do kill -0 "$HPID" 2>/dev/null || break; sleep 1; done
kill -9 "$HPID" 2>/dev/null
pkill -f "runtimes/voice/bin/python3 -I -B $RES" 2>/dev/null
echo "children remaining: $(pgrep -f "runtimes/voice/bin/python3 -I -B $RES" | wc -l | tr -d ' ')"
