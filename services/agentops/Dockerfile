# Build from the repo root: docker build -f services/agentops/Dockerfile -t idea-validator-agentops:$(git rev-parse --short HEAD) .
# Rubrics, prompts and rule files are not baked in: ConfigMaps (or compose mounts) provide them.
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS build
RUN python -m venv --without-pip /venv
COPY services/agentops/requirements.txt /tmp/requirements.txt
RUN pip --python /venv/bin/python install --no-cache-dir -r /tmp/requirements.txt
COPY libs/common /tmp/common
RUN pip --python /venv/bin/python install --no-cache-dir --no-deps /tmp/common

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
# a new named volume copies this ownership, so the app user can write traces
RUN useradd --uid 10001 --create-home app && mkdir -p /data/traces && chown 10001 /data/traces
ENV PATH=/venv/bin:$PATH PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
  AGENTOPS_TRACES_DIR=/data/traces AGENTOPS_RUBRICS_DIR=/etc/rubrics \
  AGENTOPS_PROMPTS_DIR=/etc/prompts AGENTOPS_CONFIG_DIR=/etc/ideaval-config
COPY --from=build /venv /venv
COPY services/agentops/src /app
# numeric, so Kubernetes can verify runAsNonRoot
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2)"]
CMD ["uvicorn", "agentops.main:app", "--host", "0.0.0.0", "--port", "8000"]
