#!/bin/zsh
# The laptop server for the team page, restarted at once if it ever stops (a crash must never take the demo down).
#   MODAL_PROFILE=oliver-universitapps library/run_team_server.sh        (from the repo root)
PY=${BOB_PY:-python3}
while true; do
  PORT=${PORT:-8770} MODAL_PROFILE=${MODAL_PROFILE:-oliver-universitapps} "$PY" library/team_server.py
  echo "[bob] team server stopped ($?); restarting in 1 s" >&2
  sleep 1
done
