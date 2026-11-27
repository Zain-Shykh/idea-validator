#!/usr/bin/env bash
# Run one implementation phase in a headless Claude Code session (docs/workflow.md §5).
# Usage: scripts/run-phase.sh <PHASE_ID> [extra instructions...]
#   PERMISSION_MODE=bypassPermissions scripts/run-phase.sh Z2   # if 'auto' blocks needed commands
set -euo pipefail

phase="${1:?usage: scripts/run-phase.sh <PHASE_ID> [extra instructions]}"; shift
root="$(git rev-parse --show-toplevel)"; cd "$root"

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree not clean; commit or stash first." >&2; exit 1
fi

# Prompt = text between the HEADLESS-PROMPT markers in docs/workflow.md (code fences dropped).
prompt="$(sed -n '/<!-- HEADLESS-PROMPT:START -->/,/<!-- HEADLESS-PROMPT:END -->/p' docs/workflow.md \
  | sed '1d;$d' | grep -v '^```' | sed "s/{{PHASE}}/${phase}/g")"
[ -n "$prompt" ] || { echo "HEADLESS-PROMPT markers not found in docs/workflow.md" >&2; exit 1; }
[ $# -gt 0 ] && prompt+=$'\n\nEXTRA INSTRUCTIONS FROM THE OWNER: '"$*"

mkdir -p .claude-runs
log=".claude-runs/${phase}-$(date +%Y%m%d-%H%M%S).jsonl"
echo "Phase ${phase}: headless session started (permission mode: ${PERMISSION_MODE:-auto})"
echo "Log: ${log}   (live: tail -f ${log} | jq -r 'select(.type==\"assistant\") | .message.content[]? | select(.type==\"text\") | .text')"

claude -p "$prompt" \
  --permission-mode "${PERMISSION_MODE:-auto}" \
  -n "phase-${phase}" \
  --output-format stream-json --verbose > "$log"

# Print the session's final message.
tail -n 1 "$log" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("result", d))'
