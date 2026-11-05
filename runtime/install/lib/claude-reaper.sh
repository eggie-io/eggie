#!/usr/bin/env bash
# Stops Claude Code Desktop session processes whose transcript has not
# changed for EGGIE_REAPER_IDLE_MIN minutes. Desktop keeps one per session
# for up to 30 days; the transcript stays, and the next message starts a
# fresh process. Run as root from eggie-claude-reaper.timer.
# Anything it cannot tie to a transcript is logged and left alone.
set -uo pipefail

PROC=${EGGIE_REAPER_PROC:-/proc}
IDLE_MIN=${EGGIE_REAPER_IDLE_MIN:-120}
DRY=${EGGIE_REAPER_DRY:-0}

log() { logger -t eggie-reaper "$*"; }

# A non-number would read as 0 in (( )) and reap every session.
if [[ ! $IDLE_MIN =~ ^[0-9]+$ ]] || (( IDLE_MIN < 1 )); then
  log "EGGIE_REAPER_IDLE_MIN=$IDLE_MIN is not a positive number of minutes, nothing reaped"
  exit 1
fi
[[ $DRY == 0 ]] || DRY=1

# Both --session-id=<uuid> and --session-id <uuid>; --resume the same way.
session_id() {
  tr '\0' '\n' < "$1/cmdline" 2>/dev/null | awk '
    want && /^[0-9a-f-]{36}$/ { print; exit }
    { want = 0 }
    /^--(session-id|resume)=[0-9a-f-]{36}$/ { sub(/^[^=]*=/, ""); print; exit }
    /^--(session-id|resume)$/ { want = 1 }'
}

now=$(date +%s)
for d in "$PROC"/[0-9]*; do
  pid=${d##*/}
  # A binary Desktop has since replaced reads "... (deleted)"; still matches.
  [[ $(readlink "$d/exe" 2>/dev/null) == */.claude/remote/ccd-cli/* ]] || continue
  sid=$(session_id "$d")
  [[ -n $sid ]] || { log "pid=$pid no session id, skip"; continue; }
  home=$(getent passwd "$(stat -c %u "$d")" | cut -d: -f6)
  f=$(ls -t "$home"/.claude/projects/*/"$sid".jsonl 2>/dev/null | head -1)
  [[ -n $home && -n $f ]] || { log "pid=$pid session=$sid no transcript, skip"; continue; }
  idle=$(( (now - $(stat -c %Y "$f")) / 60 ))
  log "pid=$pid session=$sid idle_min=$idle"
  if (( idle >= IDLE_MIN )); then
    (( DRY )) || kill -TERM "$pid" 2>/dev/null
    log "reap pid=$pid session=$sid idle_min=$idle dry=$DRY"
  fi
done
exit 0
