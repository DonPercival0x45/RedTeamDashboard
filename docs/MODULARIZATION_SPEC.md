# RedTeamDashboard Modularization Project Specification

## Executive Summary

**Objective:** Transform RedTeamDashboard from a monolithic application into a modular, plugin-based architecture where features can be added, removed, or disabled without affecting core functionality.

**Timeline:** 10-12 weeks  
**Impact:** Reduces technical debt, enables faster feature development, improves maintainability, and allows deployment flexibility.

---

## Current State Analysis

### Anti-Patterns Identified

**Load-Bearing Features:**
- Nessus importer tightly coupled to findings validation
- Cost tracking embedded in engagement workflows  
- Entra SSO as only authentication mechanism
- Strategic agent hardcoded into task processing
- Tools directly referenced in orchestrator code

**Technical Debt:**
- Feature code scattered across multiple modules
- No clear separation between core and peripheral functionality
- Difficult to test features in isolation
- Cannot disable features without code changes
- New integrations require modifying core code

---

## Target Architecture

### Design Principles

1. **Interface Segregation:** Core services defined as abstract interfaces
2. **Plugin Pattern:** Extensible modules for integrations and features
3. **Feature Flags:** Configuration-driven feature enablement
4. **Event-Driven:** Decoupled communication via publish-subscribe
5. **Dependency Injection:** Swappable implementations
6. **Configuration Over Code:** Behavior changes via config, not deployment

### Core Components

**1. Plugin System**
- Base plugin interface with specialized types (scanners, auth, orchestrators)
- Plugin registry and discovery system
- Configuration-based plugin enablement

**2. Feature Flag System**
- YAML-based feature configuration
- Runtime feature dependency resolution
- Gradual rollout support with percentage-based enablement

**3. Core Service Interfaces**
- Abstract interfaces for orchestrator, auth, cost tracking, finding storage
- Dependency injection container
- Swappable implementations

**4. Event Bus System**
- Publish-subscribe pattern for decoupled communication
- Event replay for debugging
- Standardized event types

---


---

## Pull-Based Deployment Architecture

### Current Deployment Model (Push-Based)

**Current Process:**
```bash
# Current: You push updates to Azure
./install.sh --env prod --location centralus --yes
# or
gh workflow run deploy.yml  # Pushes image + updates Container Apps
```

**Security & Operational Issues:**
- ❌ Requires Azure credentials in GitHub Actions
- ❌ CI/CD system needs write access to subscription
- ❌ Manual triggering required for deployments
- ❌ Service principal credentials at risk
- ❌ Centralized deployment bottleneck
- ❌ Harder to implement per-environment controls

### Pull-Based Deployment Options

#### **Option 1: Container Apps Image Monitoring** ⭐ **Recommended**

Container Apps can automatically pull new images when detected in the registry.

**Implementation Steps:**
```bash
# 1. Create Azure Container Registry
az acr create -g rtd-prod -n rtdprodcacr --sku Standard

# 2. Configure GitHub Actions to push to ACR
# .github/workflows/build.yml
- name: Push to ACR
  run: |
    az acr login -n rtdprodcacr
    VERSION=$(git describe --tags --always)
    docker build -t rtdprodcacr.azurecr.io/rtd-backend:$VERSION backend/
    docker push rtdprodcacr.azurecr.io/rtd-backend:$VERSION

# 3. Container Apps automatically pulls when new image detected
# No Azure credentials needed in GitHub Actions!
```

#### **Option 2: GitHub Container Registry + Azure Webhook**

Use GHCR with Azure webhook notifications instead of direct updates.

```yaml
# .github/workflows/deploy.yml
on:
  push:
    branches: [main]

jobs:
  build-and-notify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      # Build and push to GHCR (already setup)
      - name: Build and push
        run: |
          VERSION=$(git describe --tags --always)
          docker build -t ghcr.io/DonPercival0x45/rtd-backend:$VERSION backend/
          echo ${{ secrets.GITHUB_TOKEN }} | docker login ghcr.io -u ${{ github.actor }} --password-stdin
          docker push ghcr.io/DonPercival0x45/rtd-backend:$VERSION
      
      # Notify Azure to pull (instead of pushing)
      - name: Trigger Azure deployment
        run: |
          curl -X POST '${{ secrets.AZURE_WEBHOOK_URL }}' \
            -H 'Content-Type: application/json' \
            -d '{"image": "ghcr.io/DonPercival0x45/rtd-backend:$VERSION", "environment": "production"}'
```

#### **Option 3: Self-Updating Containers**

Containers check for updates and restart themselves autonomously.

### Recommended Implementation Strategy

#### **Hybrid Pull + Push Model (Best of Both Worlds)**

**Development/QA: Pull-Based (Automatic)**
```yaml
# config/environments.yaml
environments:
  dev:
    update_mode: pull
    trigger: push_to_main
    auto_deploy: true
    approval_required: false
    
  qa:
    update_mode: pull  
    trigger: push_to_main
    auto_deploy: true
    approval_required: false
```

**Staging: Pull-Based (Manual Approval)**
```yaml
  staging:
    update_mode: pull
    trigger: manual_approval
    auto_deploy: false
    approval_required: true
    approvers: ["tech-lead", "devops-lead"]
```

**Production: Pull-Based (Strict Controls)**
```yaml
  prod:
    update_mode: pull
    trigger: manual_approval
    auto_deploy: false
    approval_required: true
    approvers: ["production-lead", "security-lead"]
    maintenance_window: "02:00-04:00 UTC"
```

### Security Benefits

**Pull-Based Security Advantages:**
- ✅ **No Azure credentials in GitHub Actions** - ACR webhooks handle authentication
- ✅ **No service principals with write access** - Environments pull when ready
- ✅ **Reduced attack surface** - Registry compromise vs full subscription access
- ✅ **Better audit trail** - Clear logs of what each environment pulled
- ✅ **Automatic rollback** - Health checks trigger immediate rollback on failure
- ✅ **Per-environment control** - Each environment controls when to update

**Security Comparison:**
| Aspect | Push-Based | Pull-Based |
|---|---|---|
| Credentials Needed | Azure Service Principal (write access) | ACR webhook secrets (read-only) |
| Attack Surface | CI/CD compromise = subscription compromise | Registry compromise only |
| Credential Exposure | Stored in GitHub Secrets | Stored in Azure Key Vault |
| Audit Trail | Manual trigger logs | Automatic pull logs + health events |
| Rollback Capability | Manual | Automatic health check failures |
| Environment Control | Centralized (CI/CD) | Decentralized (per-environment) |
| Compliance Risk | High (broad access) | Low (minimal access) |

### Updated Implementation Timeline

**Phase 0.5: Pull-Based Deployment Infrastructure** (Add 2 weeks)

**Objectives:** Establish pull-based deployment infrastructure before modularization

**Deliverables:**
1. Azure Container Registry setup
2. GitHub Actions ACR integration
3. Environment-based deployment configuration
4. Webhook security implementation
5. Health check and automatic rollback

**Success Criteria:**
- Deployments trigger via registry webhooks
- No Azure credentials in GitHub Actions
- Automatic rollback on health check failures
- Per-environment deployment controls working
- Dev/QA auto-deploy, prod requires approval

**Tasks:**
- [ ] Create Azure Container Registry
- [ ] Configure GitHub Actions for ACR pushes
- [ ] Implement webhook security validation
- [ ] Create environment-based deployment configuration
- [ ] Add health check endpoints
- [ ] Implement automatic rollback mechanism
- [ ] Update deployment documentation
- [ ] Test pull-based deployments in all environments

**Updated Timeline:**
- **Phase 0:** Compatibility foundation (2 weeks)
- **Phase 0.5:** Pull-based deployment infrastructure (2 weeks) ← **NEW**
- **Phase 1-6:** Original modularization phases (12 weeks)
- **Phase 7:** Production migration & testing (4 weeks)

**Total: 20-22 weeks for complete migration with pull-based deployments**

### Migration Strategy

**Stage 1: Deploy Pull Infrastructure (Safe)**
```bash
# Deploy ACR and webhook infrastructure
./install.sh --env prod --location centralus \
    --deployment-mode pull \
    --yes
```

**Stage 2: Enable Pull in Development (Test)**
```yaml
# Update dev environment to pull mode
environments:
  dev:
    update_mode: pull
    auto_deploy: true
```

**Stage 3: Enable Pull in Production (Gradual)**
```yaml
# Start with manual approval, then auto
environments:
  prod:
    update_mode: pull
    auto_deploy: false  # Start with manual
    approval_required: true
```

### Rollback Strategy

**If Pull-Based Deployment Fails:**
```bash
# Immediate rollback to previous image
az containerapp update -n rtd-prod-app -g rtd-prod \
    --image rtdprodcacr.azurecr.io/rtd-backend:previous-stable

# Disable pull mode temporarily
az containerapp update -n rtd-prod-app -g rtd-prod \
    --set-env DEPLOYMENT_MODE=manual

# Or revert to previous revision
REV=$(az containerapp revision list -n rtd-prod-app -g rtd-prod \
    --query "[?properties.active].name | [0]" -o tsv)
az containerapp revision activate -n rtd-prod-app -g rtd-prod \
    --revision $REV
```

**Automatic Rollback Triggers:**
- Health check failures (3 consecutive failures)
- Startup time > 5 minutes
- Error rate > 50% for 5 minutes
- Memory/CPU limits exceeded

## Implementation Phases

### Phase 1: Foundation (Weeks 1-2)
- Core service interfaces
- Service container with dependency injection
- Base plugin interface and registry
- Event bus basic implementation
- Feature flag manager skeleton

### Phase 2: Plugin System (Weeks 3-4)
- Migrate Nessus importer to scanner plugin
- Migrate Entra auth to auth plugin
- Migrate Strategic agent to orchestrator plugin
- Plugin discovery system

### Phase 3: Feature Flags (Weeks 5-6)
- Feature flag configuration system
- Runtime feature checks throughout codebase
- Admin UI for feature management
- Feature dependency resolution

### Phase 4: Event-Driven Architecture (Weeks 7-8)
- Core event types defined
- Event handlers for key workflows
- Event replay capability
- Performance optimization

### Phase 5: Frontend Modularity (Weeks 9-10)
- Feature-aware React components
- Dynamic imports for code splitting
- Feature-specific route guards
- Plugin UI registration system

### Phase 6: Testing & Documentation (Weeks 11-12)
- Integration test suite
- Plugin development guide
- Feature flag usage guide
- Migration guide for existing code

---

## Success Criteria

**Technical Metrics:**
- 100% of integrations as plugins
- 80% of features behind feature flags
- 0% load-bearing features
- No performance degradation vs current system

**Operational Metrics:**
- Can enable/disable features via config change
- Can add new plugins without core code changes
- Can swap implementations via dependency injection

**Development Velocity:**
- New integrations take < 4 hours to implement
- New features don't require modifying core code
- Plugin development requires no backend knowledge

---

## Risk Mitigation

**Technical Risks:**
- Performance degradation from abstraction layers
- Complexity of plugin system
- Event system becoming bottleneck

**Operational Risks:**
- Feature flag conflicts
- Plugin version conflicts

**Development Risks:**
- Refactoring breaking existing functionality
- Team adoption of new patterns

---

## Conclusion

This modularization project will transform RedTeamDashboard from a monolithic application into a flexible, plugin-based architecture. The phased approach minimizes risk while delivering value incrementally.

**Key Benefits:**
- Faster feature development
- Safer deployments and rollbacks
- Better code organization
- Extensible integration points
- Future-proof architecture

---

*Document Version: 1.0*  
*Last Updated: 2026-06-24*  
*Author: Modularization Project Team*
