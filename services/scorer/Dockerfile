# Build from the repo root: docker build -f services/scorer/Dockerfile -t idea-validator-scorer:$(git rev-parse --short HEAD) .
# The steps up to COPY /venv are the same in every service, so images share those layers.
# No model weights, rubrics or prompts are baked in: a PVC and ConfigMaps (or mounts) provide them.
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS build
RUN python -m venv --without-pip /venv
COPY services/scorer/requirements.txt /tmp/requirements.txt
RUN pip --python /venv/bin/python install --no-cache-dir -r /tmp/requirements.txt
COPY libs/common /tmp/common
RUN pip --python /venv/bin/python install --no-cache-dir --no-deps /tmp/common

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
RUN useradd --uid 10001 --create-home app
ENV PATH=/venv/bin:$PATH PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
  SCORER_RUBRICS_DIR=/etc/rubrics SCORER_PROMPTS_DIR=/etc/prompts
COPY --from=build /venv /venv
COPY services/scorer/src /app
# numeric, so Kubernetes can verify runAsNonRoot
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2)"]
CMD ["uvicorn", "scorer.main:app", "--host", "0.0.0.0", "--port", "8000"]
