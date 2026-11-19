#!/bin/zsh
# Supported stop/start pair for one launchd login item. Never touches any other label.
emulate -R zsh
setopt no_nomatch

label='kh.studiohub.standalone'
health=''
plist=''
usage() {
  print -r -- "usage: ${0:t} stop|start|verify [--label LABEL] [--health URL] [--plist PATH]"
}

cmd=${1-}
(( $# > 0 )) && shift
case $cmd in
  stop|start|verify) ;;
  -h|--help|'') usage; exit 0 ;;
  *) print -u2 -- "ERROR: unknown command '$cmd'"; usage; exit 2 ;;
esac

while (( $# )); do
  case $1 in
    --label)  (( $# >= 2 )) || { print -u2 -- 'ERROR: --label needs a value'; exit 2; }; label=$2; shift 2 ;;
    --health) (( $# >= 2 )) || { print -u2 -- 'ERROR: --health needs a value'; exit 2; }; health=$2; shift 2 ;;
    --plist)  (( $# >= 2 )) || { print -u2 -- 'ERROR: --plist needs a value'; exit 2; }; plist=$2; shift 2 ;;
    *) print -u2 -- "ERROR: unknown argument '$1'"; usage; exit 2 ;;
  esac
done

[[ $label =~ '^[A-Za-z0-9._-]+$' ]] || { print -u2 -- "ERROR: unsafe label '$label'"; exit 2; }
uid=$(/usr/bin/id -u)
domain="gui/$uid"
target="$domain/$label"
[[ -n $plist ]] || plist="$HOME/Library/LaunchAgents/$label.plist"
integer bound=30

die() { print -u2 -- "FAIL: $1"; exit 1; }

capture_print() {
  PRINT_RAW=$(/bin/launchctl print "$target" 2>&1)
  PRINT_STATUS=$?
  PRINT_STATE=''; PRINT_PID=''; PRINT_EXIT=''; PRINT_ABSENT=0
  local line
  for line in ${(f)PRINT_RAW}; do
    # launchctl also prints indented endpoint states ("state = active"); the first is the job's.
    [[ -z $PRINT_STATE && $line =~ '^[[:space:]]*state[[:space:]]*=[[:space:]]*(.*)$' ]] && PRINT_STATE=$match[1]
    [[ -z $PRINT_PID && $line =~ '^[[:space:]]*pid[[:space:]]*=[[:space:]]*([0-9]+)' ]] && PRINT_PID=$match[1]
    [[ -z $PRINT_EXIT && $line =~ '^[[:space:]]*last exit code[[:space:]]*=[[:space:]]*(.*)$' ]] && PRINT_EXIT=$match[1]
  done
  [[ $PRINT_RAW == *'Could not find service'* || $PRINT_RAW == *'No such process'* || $PRINT_RAW == *'not found'* ]] && PRINT_ABSENT=1
  (( PRINT_STATUS != 0 )) && PRINT_ABSENT=1
}

report_print() {
  local prefix=$1 line trimmed seen=0
  for line in ${(f)PRINT_RAW}; do
    case $line in
      *'state = '*|*'pid = '*|*'last exit code = '*|*'Could not find service'*|*'not found'*|*'spawn failed'*|*'LWCR'*)
        [[ $line =~ '^[[:space:]]*(.*)$' ]] && trimmed=$match[1] || trimmed=$line
        print -r -- "$prefix launchctl: $trimmed"; seen=1 ;;
    esac
  done
  (( seen )) || print -r -- "$prefix launchctl: (exit $PRINT_STATUS) ${PRINT_RAW//$'\n'/ | }"
}

health_probe() {
  HEALTH_BODY=''; HEALTH_PID=''; HEALTH_STATUS=1
  [[ -n $health ]] || return 2
  HEALTH_BODY=$(/usr/bin/curl -fsS --connect-timeout 1 --max-time 2 -- "$health" 2>&1)
  HEALTH_STATUS=$?
  if (( HEALTH_STATUS == 0 )); then
    if [[ $HEALTH_BODY =~ '"pid"[[:space:]]*:[[:space:]]*([0-9]+)' ]]; then HEALTH_PID=$match[1]
    else HEALTH_PID='none'; fi
  fi
}

report_health() {
  local prefix=$1
  if [[ -z $health ]]; then print -r -- "$prefix health: NOT CHECKED (no --health supplied)"; return; fi
  if (( HEALTH_STATUS == 0 )); then
    print -r -- "$prefix health: answering pid=$HEALTH_PID"
  else
    print -r -- "$prefix health: no answer (curl exit $HEALTH_STATUS) ${HEALTH_BODY//$'\n'/ | }"
  fi
}

fail_with_state() {
  capture_print
  report_print 'state:'
  die "$1 State: ${PRINT_STATE:-not found}. Do this: $2"
}

print -r -- "$cmd label=$label target=$target plist=$plist"

case $cmd in
  stop)
    out=$(/bin/launchctl bootout "$target" 2>&1); rc=$?
    print -r -- "bootout exit=$rc ${out//$'\n'/ | }"
    if (( rc != 0 && rc != 3 )); then
      fail_with_state "bootout failed (exit $rc)." "Retry stop; if it keeps failing, quit the app from its menu and run stop again. Nothing was deleted."
    fi
    integer deadline=$(( SECONDS + bound )); attempt=0; gone=0
    while (( SECONDS < deadline )); do
      capture_print; attempt=$(( attempt + 1 )); report_print "stop poll $attempt:"
      if (( PRINT_ABSENT )); then gone=1; break; fi
      /bin/sleep 0.5
    done
    (( gone )) || fail_with_state "service is still loaded after ${bound}s." "Quit the app from its menu, then rerun stop; do not swap the bundle while it is loaded."
    print -r -- "stop: service absent from $target after ${attempt} poll(s)"
    if [[ -n $health ]]; then
      deadline=$(( SECONDS + bound )); attempt=0; quiet=0
      while (( SECONDS < deadline )); do
        health_probe; attempt=$(( attempt + 1 )); report_health "stop health poll $attempt:"
        (( HEALTH_STATUS != 0 )) && { quiet=1; break; }
        /bin/sleep 0.5
      done
      (( quiet )) || { capture_print; report_print 'state:'; die "STILL ALIVE after ${bound}s: health still answers pid=${HEALTH_PID:-none} while launchctl state=${PRINT_STATE:-not found}. Do this: quit the app from its menu, rerun stop, and do not swap the bundle yet."; }
      print -r -- "stop: health stopped answering after ${attempt} poll(s)"
    else
      report_health 'stop:'
    fi
    ;;

  start)
    [[ -f $plist ]] || fail_with_state "plist missing at $plist." "Restore the original plist, then rerun start with --plist pointing at it."
    out=$(/usr/bin/plutil -lint "$plist" 2>&1); rc=$?
    print -r -- "plutil -lint exit=$rc ${out//$'\n'/ | }"
    (( rc == 0 )) || fail_with_state "plist failed lint." "Restore the original plist (or fix the XML), then rerun start with --plist pointing at it."
    out=$(/bin/launchctl enable "$target" 2>&1); rc=$?
    print -r -- "enable exit=$rc ${out//$'\n'/ | }"
    (( rc == 0 )) || print -u2 -- "NOTE: enable returned $rc; continuing to bootstrap (a stale disabled override can follow an old code requirement)."
    out=$(/bin/launchctl bootstrap "$domain" "$plist" 2>&1); rc=$?
    print -r -- "bootstrap exit=$rc ${out//$'\n'/ | }"
    if (( rc != 0 )); then
      capture_print
      report_print 'state:'
      die "bootstrap failed (exit $rc). Machine state: no service loaded for $label. State: ${PRINT_STATE:-not found}. Do this: restore the original plist and rerun start with --plist pointing at it before walking away; if the output says 'spawn failed' or 'needs LWCR update', reload the job after the binary swap (bootout, enable, bootstrap)."
    fi
    integer deadline=$(( SECONDS + bound )); attempt=0; running=0
    while (( SECONDS < deadline )); do
      capture_print; attempt=$(( attempt + 1 )); report_print "start poll $attempt:"
      if [[ $PRINT_STATE == *running* && -n $PRINT_PID ]]; then running=1; break; fi
      /bin/sleep 0.5
    done
    (( running )) || fail_with_state "no running pid after ${bound}s." "Read 'launchctl print $target'; if it says spawn failed or needs LWCR update, restore the original plist and rerun start; do not leave the machine with no login item and no message."
    print -r -- "start: running pid=$PRINT_PID"
    if [[ -n $health ]]; then
      deadline=$(( SECONDS + bound )); attempt=0; matched=0
      while (( SECONDS < deadline )); do
        health_probe; attempt=$(( attempt + 1 )); report_health "start health poll $attempt:"
        if (( HEALTH_STATUS == 0 )) && [[ $HEALTH_PID == $PRINT_PID ]]; then matched=1; break; fi
        /bin/sleep 0.5
      done
      (( matched )) || { health_probe; report_health 'state:'; capture_print; report_print 'state:'; die "health did not answer with launchctl pid $PRINT_PID after ${bound}s (health pid=${HEALTH_PID:-none}; launchctl state=${PRINT_STATE:-not found}). Do this: check $health and the app's own log; if state says spawn failed or needs LWCR update, restore the original plist and rerun start."; }
      print -r -- "start: health confirms pid=$PRINT_PID after ${attempt} poll(s)"
    else
      report_health 'start:'
    fi
    ;;

  verify)
    capture_print
    report_print 'verify:'
    print -r -- "state: ${PRINT_STATE:-not found}"
    print -r -- "pid: ${PRINT_PID:-none}"
    print -r -- "last exit code: ${PRINT_EXIT:-none}"
    health_probe
    if (( HEALTH_STATUS == 0 )); then print -r -- "health pid: ${HEALTH_PID:-none}"
    else print -r -- "health pid: no answer (curl exit $HEALTH_STATUS)"; fi
    ;;
esac
