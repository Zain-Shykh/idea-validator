#!/usr/bin/env bash
# Put the STAND-IN for M2, M5 and M6 into the cluster, or take it out again (see standin.py).
#   KUBE_CONTEXT=idea-validator scripts/standins/run.sh up <image-tag>
#   KUBE_CONTEXT=idea-validator scripts/standins/run.sh down
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${KUBE_CONTEXT:?name the cluster, so the stand-in cannot land in another one}"
kc() { kubectl --context "$KUBE_CONTEXT" -n "${NAMESPACE:-idea-validator}" "$@"; }
manifest=scripts/standins/standin.yaml

case "${1:-}" in
  up)
    tag=${2:?usage: run.sh up <image-tag>}
    # never take over a Service a real M2, M5 or M6 already has
    for name in profile kb tools; do
      found=$(kc get service "$name" --ignore-not-found \
        -o 'jsonpath={.metadata.name}:{.metadata.labels.idea-validator/stand-in}')
      if [ -n "$found" ] && [ "$found" != "$name:true" ]; then
        echo "error: a real Service '$name' exists; the stand-in will not replace it" >&2
        exit 1
      fi
    done
    kc create configmap standin-code --from-file=scripts/standins/standin.py \
      --dry-run=client -o yaml | kc apply -f -
    kc create configmap standin-cards \
      --from-file=eval/fixtures/cards_small.jsonl --from-file=eval/fixtures/validator_cards.jsonl \
      --dry-run=client -o yaml | kc apply -f -
    kc label configmap standin-code standin-cards idea-validator/stand-in=true --overwrite
    sed "s|:set-by-run-sh|:$tag|" "$manifest" | kc apply -f -
    # the same tag can hold new code
    kc rollout restart deployment/standin
    kc rollout status deployment/standin --timeout=120s
    ;;
  down)
    kc delete deployment,service,configmap -l idea-validator/stand-in=true --ignore-not-found
    ;;
  *)
    echo "usage: $0 up <image-tag> | down" >&2
    exit 2
    ;;
esac
