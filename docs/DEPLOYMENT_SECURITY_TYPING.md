# Deployment Security & Incremental Typing Improvements

## Overview
Focused improvements addressing concrete pain points rather than hypothetical future flexibility.

**Total scope: 3-4 weeks**

---

## 1. Pull-based ACR Deployment (2 weeks)

### Problem Statement
- GitHub Actions has service principal with Container Apps Contributor access
- CI/CD compromise = subscription-level write access
- Azure credentials in CI/CD represent genuine security risk

### Solution
- Azure Container Registry (ACR) setup
- Webhook-based deployment triggers
- Remove Azure credentials from GitHub Actions
- Per-environment deployment controls

### Security Benefits
- No Azure credentials in CI/CD
- Registry-level vs subscription-level access
- Better audit trail and automatic rollback

---

## 2. ImporterProtocol (when source #4 lands)

### When Needed
- When fourth importer source is required

### Solution
- Single typing.Protocol file (~2 days)
- Type safety for importer contract
- No plugin framework needed

---

## 3. ExecutorProtocol (when ACA Jobs land)

### When Needed
- When ACA Jobs feature requires execution substrate contracts

### Solution
- Single typing.Protocol file (~2 days)
- Lease contract type validation
- Substrate pluggability

---

## 4. Feature Flag Helper (when Stage 3 ramps)

### When Needed
- When Stage 3 needs to ramp container routing

### Solution
- Simple env var + helper function (~1 day)
- Safe rollout of risky routing changes

---

## Philosophy

Follow project principle: 'Don'\''t add features, refactor, or introduce abstractions beyond what the task requires.'

Each piece independently justifiable. No premature abstractions.

---

*Document Version: 1.0*
*Based on feedback rejecting premature modularization*
