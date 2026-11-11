# Neuron Atlas Interface - API server, web UI and MCP endpoint in one container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ATLAS_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[mcp]"

RUN useradd --create-home --uid 10001 atlas && mkdir -p /data && chown atlas:atlas /data
USER atlas
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["neuron-atlas", "serve", "--host", "0.0.0.0", "--port", "8000"]
