# Render Migration Handoff

Current date: 2026-05-08

This file is the execution handoff for migrating Nimbus from Fly.io to Render.
It is intentionally written for a fresh Codex session: start here, then read
`AGENTS.md`, `NIMBUS_STATUS.md`, `docs/source/deployment-operations.md`, and
`render.yaml`.

## Working System Model

Goal:

- Fully remove the Fly deployment path.
- Deploy Nimbus on Render with staging and production services.
- Use Render Postgres as the authoritative runtime state store.
- Keep the architecture extensible for later Terraform or Render Blueprint work.

Public contract:

- `/health` remains lightweight liveness and should not require cloud
  dependencies.
- `/ready` is the deployment gate and fails closed when required secrets,
  Postgres, schema, OpenRouter, or storage-tool configuration is not ready.
- `/ai/chat/turn` remains the signed wrapper contract.
- Duplicate logical turns must converge on one cached result or one explicit
  conflict, never double-execute side effects silently.

Invariants:

- Tenant/workspace/session state must not cross boundaries.
- Signed wrapper input is validated before runtime execution.
- Idempotency keys are scoped to the logical conversation/request payload.
- Destructive actions require explicit confirmation and durable state.
- Success is not visible until result/evidence state is persisted.

Failure model:

- Missing secrets: `/ready` fails and protected routes fail explicitly.
- Postgres unreachable or stale schema: `/ready` fails closed.
- Duplicate request while first is in flight: `409`.
- Same idempotency key with different payload: `409`.
- OpenRouter timeout/rate limit/provider error: mapped to domain HTTP errors.
- S3/storage failure: storage service returns stable error shape and adapter
  maps to domain exceptions.

Verification plan:

- Unit tests for readiness, feature flags, idempotency, and request-state paths.
- Runtime tests for duplicate requests, in-flight claims, and action transitions.
- Deployed smoke tests for `/health`, `/ready`, `/guide/`, unsigned
  `/ai/chat/turn`, signed `/ai/chat/turn`, and duplicate signed request replay.
- CircleCI quality and security gates before production deploy hook.

## Branch and Environment Plan

| Git branch | Render service | Deploy mode | Purpose |
|---|---|---|---|
| `hw3-stage` | `nimbus-staging` | Render auto-deploy | Rapid team iteration |
| `hw-3` | `nimbus-production` | CircleCI deploy hook | Production/demo |
| `main` | none directly | Protected PR target | Final course artifact |

The final course PR remains `hw-3 -> main`.

## Implemented In This Session

- Added `render.yaml` Blueprint with staging/production web services and Render
  Postgres databases inside a `nimbus` Render project with explicit staging and
  production environments.
- Removed Fly config/IaC/scripts:
  - `fly.toml`
  - `infra/infra_config.yml`
  - `infra/nimbus_iac.py`
  - `scripts/ci/write_fly_context.py`
  - `scripts/ci/rollback_fly_release.sh`
- Replaced CircleCI Fly deploy/rollback jobs with:
  - `security-ci` on `hw-3` only;
  - `deploy-render-production` using a Render deploy hook;
  - `/ready` wait and deployed smoke checks.
- Added `scripts/db/migrate.py` and `scripts/db/check.py`.
- Added `scripts/render/start.sh` so free-tier Render web services can run the
  idempotent Postgres migration at startup before `exec`ing Uvicorn.
- Added Postgres runtime state primitives in `nimbus_runtime.postgres`.
- Added Postgres-backed runtime stores for events, actions, and artifacts.
- Added Postgres session/request-state routing with local file fallback.
- Added `/ready` readiness checks at the root app and AI router.
- Added LaunchDarkly feature flag adapter for production kill switches.
- Added in-flight idempotent turn claims to protect duplicate wrapper delivery.
- Updated docs to make Render/Postgres the canonical deployment story.

## Render Account Setup

Ask the user to do these in Render:

1. Create a Render account at <https://render.com/>.
2. Connect the GitHub repository.
3. Create a Render project, preferably `nimbus`.
4. Create services from the Blueprint in `render.yaml`.
5. Confirm two environments/services:
   - staging from `hw3-stage`, auto-deploy enabled;
   - production from `hw-3`, auto-deploy disabled.
6. Create or confirm Render Postgres resources for staging and production.
7. Copy the production deploy hook URL into CircleCI as
   `RENDER_PRODUCTION_DEPLOY_HOOK_URL`.
8. Copy the production service URL into CircleCI as
   `RENDER_PRODUCTION_BASE_URL`.
9. Generate one strong `AI_SERVER_SIGNING_SECRET` and enter the same value in
   Render production and the CircleCI `render-production` context. Staging can
   use a different signing secret.

Production should use a paid Render Postgres plan if we want backups or
point-in-time recovery. Free Postgres is demo-only.

## Secrets and Variables

`credentials.env` contains these expected variables by name. Do not print,
commit, or paste their values:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_REGION`
- `AWS_BUCKET_NAME`
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- `OPENROUTER_FALLBACK_MODEL`

Ask teammates for:

- `NEW_RELIC_LICENSE_KEY`
- New Relic OTLP endpoint if it differs from the default
- LaunchDarkly SDK key for production

Sentry DSN has been provided out-of-band in chat. Treat it as sensitive and
enter it only as the `SENTRY_DSN` environment variable in Render. Do not commit
or paste the value into docs.

LaunchDarkly production SDK key has been provided out-of-band in chat. Treat it
as sensitive and enter it only as the `LAUNCHDARKLY_SDK_KEY` environment
variable in Render production or Doppler. Do not commit or paste the value into
docs.

Move shared values into Render environment variables and the CircleCI
`render-production` context. Use Doppler for team sharing if available.

Doppler is not configured yet because the project/config names and service token
source have not been chosen. The migration works with Render and CircleCI as the
source of truth; Doppler can be layered in once the team creates a Doppler
project and decides whether staging/prod map to `stg`/`prd` configs.

## LaunchDarkly

LaunchDarkly is production-only for `hw-3`.

Initial flags:

- `nimbus.model_turns.enabled`
- `nimbus.storage_tools.enabled`
- `nimbus.delete_actions.enabled`
- `nimbus.attachment_uploads.enabled`
- `nimbus.postgres_state.enabled`

These are kill switches and rollout gates. They do not replace HMAC auth,
actor checks, idempotency, confirmation policy, or action-state validation.

If the SDK key is absent or LaunchDarkly is unavailable, Nimbus uses safe static
defaults and emits telemetry/logs.

## CI/CD

CircleCI remains canonical for HW3.

Normal quality jobs run on all branches:

- ruff
- mypy strict
- docs build
- unit/property/fuzz/BDD/integration tests
- coverage gate

`hw-3` only:

- `e2e-tests`
- `security-ci`
- `deploy-render-production`

Security CI currently includes:

- truffleHog verified secret scan
- Semgrep CE SAST
- Safety dependency vulnerability scan
- Bandit Python security scan
- CycloneDX SBOM generation
- GuardDog report-only until baseline cleanup

Deferred:

- PyPI OIDC/Sigstore attestations
- Nix flakes
- Buildkite/Blacksmith
- Datadog
- Cloudflare

## Tests To Keep Strengthening

Add or maintain tests for:

- Postgres migration idempotency and schema version checks.
- Session save/load/delete through Postgres.
- Nonce replay and TTL cleanup.
- Idempotency replay, conflict, and in-flight duplicate claims.
- Action transition compare-and-set.
- `/health` without dependencies.
- `/ready` failure for missing DB, stale schema, and missing secrets.
- Malformed wrapper input rejected before runtime execution.
- Duplicate signed request while first is running.
- Same idempotency key with a different payload.
- Late retry after timeout.
- Provider failure after action state changes.
- Same conversation concurrent turns serialize or CAS safely.

## Explicit Non-Goals For HW3

- No Fly compatibility after migration.
- No Fly data import; start fresh on Render.
- No Kubernetes, Traefik, custom control plane, multi-region failover, or custom
  autoscaler.
- No queues/DLQ/visibility-timeout implementation yet; document future
  action-ID/lease design instead.
- No Datadog, Buildkite, Blacksmith, Cloudflare, PyPI OIDC/Sigstore, or Nix
  flakes in this migration.

## Questions To Ask The User

- Which Render team/workspace owns the project and billing?
- Should production Postgres use a paid plan now for backups/PITR?
- What exact Render service URL should CircleCI use for
  `RENDER_PRODUCTION_BASE_URL`?
- What is the Render production deploy hook URL?
- Will Doppler be used for team secret sharing, or should Render/CircleCI be
  the source of truth?
- What are the final New Relic OTLP endpoint and license key names?
- What is the Sentry DSN?
- What is the LaunchDarkly project/environment and production SDK key?
- Should `hw3-stage` also get LaunchDarkly later, or stay env/static flags?
- What branch protection rules should require `security-ci` before merging
  `hw-3 -> main`?

## Documentation Links

- Render docs: <https://render.com/docs>
- Render pricing: <https://render.com/pricing>
- Render Blueprints: <https://render.com/docs/blueprint-spec>
- Render deploy hooks: <https://render.com/docs/deploy-hooks>
- Render health checks: <https://render.com/docs/health-checks>
- Render Postgres backups: <https://render.com/docs/postgresql-backups>
- Render free limits: <https://render.com/docs/free>
- LaunchDarkly Python SDK:
  <https://launchdarkly.com/docs/sdk/server-side/python/>
- Semgrep CE in CI: <https://semgrep.dev/docs/deployment/oss-deployment>
- GuardDog: <https://github.com/DataDog/guarddog>
