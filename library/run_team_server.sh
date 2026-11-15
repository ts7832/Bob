#!/bin/zsh
# The laptop server for the team page, restarted at once if it ever stops (a crash must never take the demo down).
#   MODAL_PROFILE=oliver-universitapps library/run_team_server.sh        (from the repo root)
PY=${BOB_PY:-$HOME/BOB/venv/bin/python}     # the permanent environment (cv2, numpy, PIL, pymupdf, ifcopenshell, modal)
# Claude Code (Aleks's live reading) runs its scripts with whatever python3 is first on PATH: make it ours, which has
# cv2, numpy and PIL (15 Nov: with the system python it spent its turns writing a PNG reader and saved nothing)
export PATH="$(dirname "$PY"):$PATH"
while true; do
  PORT=${PORT:-8770} MODAL_PROFILE=${MODAL_PROFILE:-oliver-universitapps} "$PY" library/team_server.py
  echo "[bob] team server stopped ($?); restarting in 1 s" >&2
  sleep 1
done
