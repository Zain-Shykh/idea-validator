# STAND-IN for M2, M5 and M6 — for cluster checks only

M2 (profile), M5 (tools, the only way to a hosted model) and M6 (knowledge base) are not built.
This folder lets a Validate run go through Zain's deployed services anyway, to find deployment
bugs and to load the real scorer model.

**Everything it answers is made up**: cards from `eval/fixtures/`, one fixed model reply per agent
(model name `stand-in`; the claim "judge" judges nothing), data kept in memory. A verdict, score,
cost or latency of a whole run made with it is **not a result**. Only what Zain's own containers
did is real, and of those only the scorer pod does its real work (the real base model).
`scripts/load_validate.py` finds the stand-in by itself and marks its report `"stand_in": true`.

```bash
KUBE_CONTEXT=idea-validator scripts/standins/run.sh up <image-tag>
KUBE_CONTEXT=idea-validator scripts/standins/run.sh down      # always, when the check is done
kubectl --context idea-validator -n idea-validator get all,cm -l idea-validator/stand-in=true
```

It is not in `k8s/`, and `eval/behaviour/test_no_fakes.py` fails if a manifest, compose file,
image or service source refers to it.
