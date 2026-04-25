# Combined image for Railway: runs Phase 1 (watcher), Phase 2-5 (MCP server),
# and Phase 6 (LiveSync bridge) under supervisord, all sharing /data/vault.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends supervisor curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy each phase's package and install in editable-equivalent mode.
COPY phase-1-ingestion /app/phase-1-ingestion
COPY phase-2-mcp-server /app/phase-2-mcp-server
COPY phase-6-livesync-bridge /app/phase-6-livesync-bridge

RUN pip install --upgrade pip \
    && pip install ./phase-1-ingestion \
    && pip install ./phase-2-mcp-server \
    && pip install ./phase-6-livesync-bridge

COPY deploy/railway/supervisord.conf /etc/supervisor/conf.d/second-brain.conf
COPY deploy/railway/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/second-brain.conf", "-n"]
