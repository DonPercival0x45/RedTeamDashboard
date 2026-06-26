FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        unzip \
        libcairo2 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# ProjectDiscovery subfinder — passive subdomain enumeration. Aggregates
# crt.sh + 30+ other sources (the dashboard's Phase 0 stub only used crt.sh
# directly, which both duplicated the standalone crt_sh tool and surfaced
# crt.sh's frequent 502s as run errors). Pinned to a known release so a
# silent upstream change doesn't break the orchestrator's tool contract.
ARG SUBFINDER_VERSION=2.14.0
RUN curl -fsSL -o /tmp/subfinder.zip \
        "https://github.com/projectdiscovery/subfinder/releases/download/v${SUBFINDER_VERSION}/subfinder_${SUBFINDER_VERSION}_linux_amd64.zip" \
 && unzip -j /tmp/subfinder.zip subfinder -d /usr/local/bin/ \
 && chmod +x /usr/local/bin/subfinder \
 && rm /tmp/subfinder.zip \
 && /usr/local/bin/subfinder -version

COPY pyproject.toml ./
RUN pip install --upgrade pip \
 && pip install -e ".[dev]"

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY tests ./tests
COPY scripts ./scripts

# Templates folder for WeasyPrint reports (used by app.api.reports).
# (Already included under COPY app, but explicit for grep-ability.)

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
