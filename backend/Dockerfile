# --- subfinder builder stage -------------------------------------------------
# Build subfinder from source against the latest patched Go toolchain. The
# pre-built release binaries on github.com/projectdiscovery/subfinder/releases
# embed whatever Go stdlib was current at release time, which means Trivy will
# flag them every time a new Go-stdlib CVE drops (and Go drops CVEs every
# month or so). Building from source against `golang:1.25` keeps the embedded
# stdlib current and clears the recurring CVE wall on the backend image.
ARG SUBFINDER_VERSION=v2.14.0
# Pin a specific patch so the embedded Go stdlib is a known-good version;
# `golang:1.25` alone drifts down to whatever the tag currently points at
# (was 1.25.3 as of 2026-07-01, which Trivy flags for a dozen stdlib CVEs).
FROM golang:1.25.11 AS subfinder-builder
ARG SUBFINDER_VERSION
ENV CGO_ENABLED=0 \
    GOFLAGS=-trimpath
RUN go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@${SUBFINDER_VERSION}

# --- docker CLI stage --------------------------------------------------------
# v0.12.0: LocalDockerRunner spawns sibling containers via the mounted
# docker socket. Debian's docker.io package ships only the daemon;
# copying the static CLI out of the official docker image is smaller
# and keeps the runtime image without dockerd.
#
# docker:29-cli embeds Go 1.26.4 — needed to clear the crypto/tls,
# crypto/x509, and net/* stdlib CVEs Trivy fails us on with older
# CLI images (docker:26-cli ships Go 1.21.11; docker:28-cli ships
# Go 1.25.3 which still trips a batch of stdlib DoS CVEs).
FROM docker:29-cli AS docker-cli

# --- runtime stage -----------------------------------------------------------
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
        libcairo2 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# v0.12.0: docker CLI copied in from a dedicated stage above. Only the
# client binary — the daemon is NOT running inside this container;
# LocalDockerRunner talks to the host daemon via the mounted socket.
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

COPY --from=subfinder-builder /go/bin/subfinder /usr/local/bin/subfinder
RUN /usr/local/bin/subfinder -version

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
