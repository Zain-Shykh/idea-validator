# M13's jobs: evaluation, drift, tuning and replay.
# Build from the repo root: docker build -f jobs/evaluation/Dockerfile -t idea-validator-evaluation:$(git rev-parse --short HEAD) --build-arg GIT_SHA=$(git rev-parse --short HEAD) .
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS build
RUN python -m venv --without-pip /venv
COPY jobs/evaluation/requirements.txt /tmp/requirements.txt
RUN pip --python /venv/bin/python install --no-cache-dir -r /tmp/requirements.txt
COPY libs/common /tmp/common
RUN pip --python /venv/bin/python install --no-cache-dir --no-deps /tmp/common

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
RUN useradd --uid 10001 --create-home app
ARG GIT_SHA=unknown
# the layout mirrors the repository, so the jobs find their files the same way in both. The
# labelled set and the rules are the commit's own: the image tag names what was evaluated
ENV PATH=/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GIT_SHA=$GIT_SHA \
    PYTHONPATH=/app/jobs/evaluation:/app/services/agentops/src:/app/services/orchestrator/src:/app/services/verdict/src \
    ORCH_RUBRICS_DIR=/app/rubrics ORCH_PROMPTS_DIR=/app/prompts \
    ORCH_BUDGETS_PATH=/app/config/budgets.yaml VERDICT_CONFIG_PATH=/app/config/verdict.yaml
COPY --from=build /venv /venv
COPY jobs/evaluation/*.py /app/jobs/evaluation/
COPY services/agentops/src /app/services/agentops/src
COPY services/orchestrator/src /app/services/orchestrator/src
COPY services/verdict/src /app/services/verdict/src
COPY eval/labelled /app/eval/labelled
COPY config /app/config
COPY rubrics /app/rubrics
COPY prompts /app/prompts
WORKDIR /app/jobs/evaluation
# numeric, so Kubernetes can verify runAsNonRoot
USER 10001
CMD ["python", "run_eval.py", "--out=/tmp/results"]
