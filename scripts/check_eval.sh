#!/usr/bin/env bash
# The nightly evaluation gate (JEN-1, AO-4). Exit 0: no worse than the baseline; 1: a metric
# regressed past its tolerance (jobs/evaluation/eval_metrics.py); anything else: no verdict on it.
#
#   scripts/check_eval.sh                                  # run the eval Job in the cluster
#   scripts/check_eval.sh --report r.json [--baseline b]   # compare a report that exists
#
# Cluster mode needs kubectl. IMAGE_TAG (default: this commit) names the evaluation image;
# KUBE_CONTEXT pins the cluster (default: kubectl's current context).
set -Eeuo pipefail  # -E: functions inherit the trap
# whatever else fails (kubectl exits 1 on most errors) must not read as a regression
trap 'exit 2' ERR
cd "$(dirname "$0")/.."

usage() {
  echo "usage: $0 [--report FILE] [--baseline FILE]" >&2
  exit 2
}

baseline=eval/results/baseline.json
report=
while [ $# -gt 0 ]; do
  case "$1" in
    --baseline) [ -n "${2:-}" ] || usage; baseline=$2; shift 2 ;;
    --report) [ -n "${2:-}" ] || usage; report=$2; shift 2 ;;
    *) usage ;;
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
kc() { kubectl ${KUBE_CONTEXT:+--context "$KUBE_CONTEXT"} -n "$ns" "$@"; }

# Kustomize deploys `app-config` under a name with a content hash and the Job is applied
# without Kustomize, so take the name from the CronJob that was deployed with it
config=$(kc get cronjob drift-report \
  -o 'jsonpath={.spec.jobTemplate.spec.template.spec.containers[0].envFrom[0].configMapRef.name}')
if [[ ! $config =~ ^app-config(-[a-z0-9]+)?$ ]]; then
  echo "error: no app-config on CronJob drift-report; deploy the overlay first" >&2
  exit 2
fi

kc create configmap eval-baseline --from-file=baseline.json="$baseline" \
  --dry-run=client -o yaml | kc apply -f -
# a finished Job cannot be changed; only this Job is removed
kc delete job eval --ignore-not-found --wait=true
sed -e "s|\(idea-validator-evaluation\):set-by-overlay|\1:$tag|" \
  -e "s|name: app-config\$|name: $config|" k8s/base/eval-job.yaml | kc apply -f -

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
  line=$(sed -n 's/^REPORT_JSON //p' "$log" | tail -n 1)
  case "$line" in
    *'"fake": true'*) echo "the report is fake: not saved" >&2 ;;
    *) printf '%s\n' "$line" >"$out"; echo "report: $out" ;;
  esac
fi
case "$state" in
  *Complete*) exit 0 ;;
esac
code=$(kc get pods -l job-name=eval \
  -o 'jsonpath={.items[0].status.containerStatuses[0].state.terminated.exitCode}')
[ "$code" = 1 ] && exit 1
exit 2
