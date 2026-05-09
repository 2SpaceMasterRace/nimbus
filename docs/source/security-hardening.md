# Security Hardening

This page is the canonical security posture for Nimbus production hardening.

## Side-Effect Authority

Public destructive or tenant-scoped mutations must go through
`nimbus_runtime` action machinery. The runtime owns actor identity, tenant
binding, policy decisions, idempotency, approval state, execution transitions,
verification, and durable evidence.

Model-facing storage tools may advertise mutation-capable names such as
`delete_file`, `copy_file`, `move_file`, and `write_file` for compatibility, but
the public wrapper path treats those calls as proposals. A tool call with
`confirm=true` is not authority. Destructive execution happens only after the
runtime records an approved action and verifies the exact tenant, actor, and
object target.

Raw storage mutation routes are internal/admin/developer surfaces. In production,
they are disabled unless `NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED=true`, and then
`NIMBUS_RAW_STORAGE_ADMIN_KEY` is required. Public product flows should use the
runtime action ledger instead.

## Encryption Guarantees

Nimbus uses layered encryption rather than claiming universal end-to-end
encryption:

- Transport: TLS/HSTS is expected at the public edge.
- Service identity: wrapper calls use HMAC signatures over method, path,
  timestamp, nonce, and body digest.
- Secrets: Slack BYOK and OAuth secrets are stored in envelope-shaped ciphertexts
  with version, key id, algorithm, tenant id, and encrypted AAD-bound payloads.
- Object storage: S3 writes use SSE-KMS when `NIMBUS_S3_KMS_KEY_ID` is set; in
  production the service is not ready without it.
- True E2EE: only applies to future workflows where clients hold keys and
  Nimbus/AI never receives plaintext. Current AI workflows require server-side
  plaintext during processing.

## Local Red-Team Tracks

Run these locally before production-facing changes:

```shell
just security-redteam
```

The local track covers:

- API/auth: replayed signatures, malformed headers, nonce exhaustion, oversized
  signed bodies, and idempotency races.
- Crypto/secrets: plaintext storage inspection, AAD mismatch, setup body caps,
  and secret-redaction checks.
- Tenant/storage: raw API mutation gates, invalid object refs, path-like keys,
  prefix confusion, and SSE-KMS assertions.
- AI/tooling: prompt-forged `confirm=true`, paraphrased destructive requests,
  and model-requested move/write/copy attempts.
- Slack/OAuth: stale timestamps, replayed events, setup-token abuse, and OAuth
  state tampering.
- Recovery/evidence: duplicate confirmations, verifier reconciliation, and
  failed destructive-operation evidence.

## Operator Checks

- `/ready` must fail in production when required auth, signing, session, or KMS
  secrets are missing.
- `/sentry-debug` is disabled by default and requires explicit debug enablement
  plus admin authorization.
- Logs, Sentry breadcrumbs, and tool-call records must not contain raw secrets,
  file content, or full object paths. Use fingerprints for correlation and keep
  full sensitive evidence inside the runtime evidence store.
