#!/bin/zsh
# Proof for hub-service-lifecycle.sh. Uses only a temp label, temp plists and temp processes.
emulate -R zsh
setopt no_nomatch

lifecycle="${0:A:h}/hub-service-lifecycle.sh"
uid=$(/usr/bin/id -u)
label="local.kh.hub-lifecycle-proof-$$"
target="gui/$uid/$label"
failures=0
cleaned=0

[[ -x $lifecycle ]] || { print -u2 -- "FAIL: $lifecycle is not executable"; exit 1; }
tmp=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/hub-lifecycle-proof.XXXXXX") || exit 1

cleanup() {
  (( cleaned )) && return
  cleaned=1
  print -r -- "--- cleanup: bootout $target and remove $tmp ---"
  /bin/launchctl bootout "$target" 2>&1 || true
  if [[ -f $tmp/server.pid ]]; then
    pid=$(< "$tmp/server.pid")
    [[ $pid =~ '^[0-9]+$' ]] && /bin/kill "$pid" 2>/dev/null || true
  fi
  /bin/rm -rf -- "$tmp"
}
trap cleanup EXIT INT TERM

port=$(/usr/bin/python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()') || exit 1
health="http://127.0.0.1:$port/api/health"

/bin/cat > "$tmp/health.py" <<'PY'
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
payload = open(sys.argv[1], 'rb').read()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *args):
        pass
class Server(HTTPServer):
    allow_reuse_address = True
Server(('127.0.0.1', int(sys.argv[2])), Handler).serve_forever()
PY

# The launchd job: reports its own pid on the health URL, then sleeps for an hour.
/bin/cat > "$tmp/job" <<EOF
#!/bin/zsh
printf '{"ok":true,"pid":%d}\n' \$\$ > "$tmp/health.json"
/usr/bin/python3 "$tmp/health.py" "$tmp/health.json" "$port" &
server=\$!
print -r -- \$server > "$tmp/server.pid"
trap 'kill \$server 2>/dev/null' EXIT INT TERM
sleep 3600
EOF
/bin/chmod 700 "$tmp/job"

/bin/cat > "$tmp/good.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>$tmp/job</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict></plist>
EOF

# Valid XML, so plutil -lint passes; launchctl cannot register a job with no Label.
/bin/cat > "$tmp/bad.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>ProgramArguments</key><array><string>/bin/true</string></array>
</dict></plist>
EOF

run() {
  local desc=$1 phrase=$2 out rc
  shift 2
  out=$("$lifecycle" "$@" 2>&1); rc=$?
  print -r -- "--- $desc (exit $rc) ---"
  print -r -- "$out"
  if [[ -n $phrase && $out != *$phrase* ]]; then
    print -u2 -- "FAIL: $desc did not contain '$phrase'"
    failures=$(( failures + 1 ))
  fi
  return $rc
}

raw_state() {
  print -r -- "--- launchctl print raw [$1] ---"
  /bin/launchctl print "$target" 2>&1 | /usr/bin/grep -E 'state = |pid = |last exit code|Could not find service|not found|spawn failed|LWCR' || true
}

print -r -- "selftest label=$label plist=$tmp/good.plist health=$health"
print -r -- "real Hub label kh.studiohub.standalone is never referenced by this selftest"

print -r -- '=== happy path: bootstrap, bootout, bootstrap again ==='
run 'start #1 (good plist)' 'health confirms pid=' start --label "$label" --plist "$tmp/good.plist" --health "$health" || failures=$(( failures + 1 ))
run 'verify after bootstrap #1' 'state: running' verify --label "$label" --health "$health" || true
raw_state 'after bootstrap #1'
run 'stop' 'stop: service absent' stop --label "$label" --health "$health" || failures=$(( failures + 1 ))
raw_state 'after bootout'
run 'verify after bootout' 'state: not found' verify --label "$label" --health "$health" || true
run 'start #2 (good plist)' 'health confirms pid=' start --label "$label" --plist "$tmp/good.plist" --health "$health" || failures=$(( failures + 1 ))
raw_state 'after bootstrap #2'
run 'verify after bootstrap #2' 'state: running' verify --label "$label" --health "$health" || true

print -r -- '=== failure path: bootout succeeds, bootstrap fails, then restore ==='
run 'stop before failed bootstrap' 'stop: service absent' stop --label "$label" --health "$health" || failures=$(( failures + 1 ))
run 'start (bad plist, expected failure)' 'Machine state: no service loaded' start --label "$label" --plist "$tmp/bad.plist" --health "$health"
rc=$?
if (( rc == 0 )); then
  print -u2 -- 'FAIL: bad plist start unexpectedly exited 0'
  failures=$(( failures + 1 ))
fi
raw_state 'after failed bootstrap'
run 'verify after failed bootstrap' 'state: not found' verify --label "$label" --health "$health" || true
run 'start recovery (good plist)' 'health confirms pid=' start --label "$label" --plist "$tmp/good.plist" --health "$health" || failures=$(( failures + 1 ))
raw_state 'after recovery bootstrap'
run 'verify after recovery' 'state: running' verify --label "$label" --health "$health" || true

print -r -- "=== result: failures=$failures ==="
(( failures == 0 )) || exit 1
exit 0
