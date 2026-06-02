# Red Team Dashboard

Multi-engagement red team operations dashboard. Each phase of an engagement
(OSINT → Scan → Verify → Exploit → PrivEsc → Persistence → Cleanup → Report)
runs as its own LangGraph ReAct agent. Operator approves active/destructive
tool calls in the UI.

## Status

Greenfield. **Phase 0 MVP** = passive OSINT vertical slice (auth →
engagement/scope CRUD → OSINT agent → findings table → SSE feed →
approvals modal → flush → PDF report).

Architecture and phase roadmap live in `docs/architecture.md` (mirror of the
approved plan).

## Stack

- Backend: Python / FastAPI (thin control plane) + LangGraph orchestrator worker
- Frontend: Next.js App Router + React + TS + Tailwind + shadcn/ui
- Data: PostgreSQL (source of truth, LangGraph checkpointer) + Redis
  (Streams jobs, pub/sub events)
- Streaming: SSE
- Hosting: AKS in prod, docker-compose locally
- LLMs: Claude Opus 4.7 (orchestrator), Claude Sonnet 4.6 (phase workers)
- Auth: Entra ID OIDC

## Layout

```
backend/    FastAPI app + LangGraph worker
frontend/   Next.js App Router
infra/      docker-compose.yml + azure/ Bicep
docs/       architecture.md
```

## Local dev

```bash
cp infra/.env.example infra/.env
docker compose -f infra/docker-compose.yml up --build
```

- Frontend: http://localhost:3001
- Backend:  http://localhost:8000/health
- Postgres: localhost:5432
- Redis:    localhost:6379
