#!/usr/bin/env bash
# The nightly evaluation gate (JEN-1, AO-4). Exit 0: no worse than the baseline; 1: a metric
# regressed past its tolerance (jobs/evaluation/eval_metrics.py); anything else: no verdict on it.
#
#   scripts/check_eval.sh                                  # run the eval Job in the cluster
#   scripts/check_eval.sh --report r.json [--baseline b]   # compare a report that exists
#
# Cluster mode needs kubectl. IMAGE_TAG (default: this commit) names the evaluation image.
set -euo pipefail
cd "$(dirname "$0")/.."

baseline=eval/results/baseline.json
report=
while [ $# -gt 0 ]; do
  case "$1" in
    --baseline) baseline=${2:?--baseline needs a file}; shift 2 ;;
    --report) report=${2:?--report needs a file}; shift 2 ;;
    *) echo "usage: $0 [--report FILE] [--baseline FILE]" >&2; exit 2 ;;
  esac
done
if [ ! -f "$baseline" ]; then
  echo "error: no baseline at $baseline (docs/specs/Z10-evaluation-tuning.md, C6)" >&2
  exit 2
fi
if [ -n "$report" ]; then
  exec "${PYTHON:-python3}" jobs/evaluation/run_eval.py --results "$report" --compare "$baseline"
fi

ns=${NAMESPACE:-idea-validator}
tag=${IMAGE_TAG:-$(git rev-parse --short HEAD)}
timeout=${EVAL_TIMEOUT_S:-14400}
kc() { kubectl -n "$ns" "$@"; }

kc create configmap eval-baseline --from-file=baseline.json="$baseline" \
  --dry-run=client -o yaml | kc apply -f -
# a finished Job cannot be changed; only this Job is removed
kc delete job eval --ignore-not-found --wait=true
sed "s|\(idea-validator-evaluation\):set-by-overlay|\1:$tag|" k8s/base/eval-job.yaml | kc apply -f -

end=$((SECONDS + timeout))
while :; do
  state=$(kc get job eval -o 'jsonpath={.status.conditions[?(@.status=="True")].type}')
  case "$state" in
    *Complete* | *Failed*) break ;;
  esac
  if [ "$SECONDS" -ge "$end" ]; then
    echo "error: the eval Job did not finish in ${timeout}s" >&2
    exit 2
  fi
  sleep "${EVAL_POLL_S:-15}"
done
log=$(mktemp)
trap 'rm -f "$log"' EXIT
kc logs job/eval --all-containers >"$log" || true
grep -v '^REPORT_JSON ' "$log" || true
# keep the report: tune.py needs it, and it becomes the next baseline
if grep -q '^REPORT_JSON ' "$log"; then
  out=${EVAL_REPORT_OUT:-eval/results/eval-$tag.json}
  sed -n 's/^REPORT_JSON //p' "$log" >"$out"
  echo "report: $out"
fi
case "$state" in
  *Complete*) exit 0 ;;
esac
code=$(kc get pods -l job-name=eval \
  -o 'jsonpath={.items[0].status.containerStatuses[0].state.terminated.exitCode}')
[ "$code" = 1 ] && exit 1
exit 2
