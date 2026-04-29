# Techniques For Testing Nimbus

Nimbus is small enough to keep tests fast, but the canonical system design makes
the future risk model clear: operation envelopes, session events, action
ledgers, projections, reconnect, retries, provider ambiguity, and load shedding
all fail in different ways.

This page gives a practical map of which testing technique fits which risk.

## Start with the boundary

Before choosing a test, name the boundary you are checking:

| Boundary | Primary risk | Useful tests |
| --- | --- | --- |
| Pure model/value object | Invalid states, serialization drift | Unit tests, property tests |
| Storage implementation | Provider exception mapping, object-name rules | Unit tests with fakes/mocks, opt-in e2e |
| HTTP storage service | Auth, status codes, response shape | FastAPI route tests, integration tests |
| Generated client adapter | HTTP-to-domain translation | Integration tests with local server/fake responses |
| AI provider implementation | Provider errors, tool loop, fallback | Unit tests with stubbed SDK |
| Runtime turn handling | Sessions, locks, confirmations, attachments | Unit tests, property tests, fuzz |
| Signed wrapper route | HMAC, nonce, idempotency, rate limits | Route tests, property tests |
| Agent-platform kernel | retries, crashes, duplicate delivery, replay, projection rebuild | Deterministic simulation testing |
| Store graduation | event append, sequence allocation, CAS, idempotency | Reusable store contract tests |
| Agent safety loop | prompt injection, policy ambiguity, model/tool drift | Golden safety fixtures and evals |

## Unit tests

Use unit tests for deterministic behavior that can be checked without real
network or cloud state.

Good Nimbus unit-test targets:

- object-name validation
- storage exception mapping
- attachment limits and digest checks
- conversation bounding
- HMAC canonical request construction
- token bucket refill behavior
- telemetry counter names
- action transition validation
- policy allow/deny/confirmation decisions

Keep unit tests direct. If a test needs a real provider, it is not a unit test.

## Integration tests

Use integration tests when behavior spans packages or transport boundaries but
can still run locally.

Good Nimbus integration targets:

- FastAPI route plus dependency override
- generated OpenAPI client plus `CloudStorageServiceAdapter`
- HTTP status mapping back to `cloud_storage_api` exceptions
- `ai_server` route plus fake `NimbusRuntime`
- runtime plus fake AI and fake storage clients

Integration tests are especially valuable where this repo preserves a public
contract across an adapter boundary.

## E2E tests

Use e2e tests for public workflows. Keep live provider e2e tests opt-in and
marked because they require credentials, network, and more patience than the
normal development loop.

Good Nimbus e2e targets:

- upload, list, download, metadata, delete through deployed storage
- signed `/ai/chat/turn` request through the service
- CLI workflow against a real OpenRouter model
- wrapper smoke test using the public HTTP contract

E2E assertions should focus on stable shape and contract, not brittle provider
phrasing.

## Property-based tests

Use property-based tests when the important claim is bigger than a few examples.

Good Nimbus properties:

- canonical request signing accepts exactly the bytes signed
- session IDs normalize or hash without path traversal
- token bucket state stays within capacity
- conversation trimming preserves recent messages and bounds token estimate
- object names never become absolute filesystem paths
- action status histories obey the transition graph
- projection replay equals live application for generated event histories
- event records reject wrong lengths, wrong digests, and unknown major versions
- idempotency lookup plus action creation behaves like one atomic decision

When a property writes to disk under Hypothesis, use a per-example
`TemporaryDirectory()` inside the test body so examples do not contaminate each
other.

## Fuzz harnesses

Use fuzzing for parsers and recovery paths that must survive malformed bytes.

Good Nimbus fuzz targets:

- conversation JSON loading
- session file parsing
- request-state file parsing
- attachment/base64 decode boundaries
- future event log parsing

Fuzz files should stay under `fuzz/` and should not contain production logic.
The CI smoke mode is for catching parser crashes, not proving every invariant.

## BDD acceptance tests

Use BDD when product language matters and the behavior crosses several layers.

Good Nimbus scenarios:

- "A user asks to delete a file and must confirm before deletion."
- "A repeated Slack event returns the same chat response."
- "A wrong actor cannot confirm another actor's delete."
- "An upload with a digest mismatch fails before provider I/O."
- "A wrapper can fetch session history with management API auth."

BDD is most useful when it creates shared language between product behavior,
runtime state, and tests.

## Golden safety and eval fixtures

Agent behavior needs stable safety fixtures in addition to ordinary tests.

Useful corpora:

- malicious object names that contain instructions
- prompts that ask the model to bypass confirmation
- ambiguous delete requests
- cross-tenant IDs in action references
- filenames with path traversal or Unicode confusion
- provider errors that should produce degraded responses

The expected output should be a policy/runtime decision, not a fragile exact
model sentence.

The canonical design splits this into four corpora:

- golden set: known prompt-injection and policy cases
- adversarial set: generated malicious prompts and object names
- uncertainty set: ambiguous cases where policy/model behavior needs review
- incident set: real production or support cases folded back into tests

## Deterministic simulation testing

DST is the long-term answer for the session/action kernel. It should generate
or script histories where scheduling, time, retries, crashes, provider
responses, and duplicate delivery are controlled by a seed.

Start small:

1. Duplicate signed request creates one operation.
2. Wrong actor cannot authorize delete.
3. Provider deletes object then times out.
4. Worker crashes after claiming action.
5. Client reconnects from an old sequence.
6. Replay projection equals live projection.
7. Event record is truncated or has a wrong digest.
8. Projection cache is corrupted and rebuilt from the event log.

DST should live near `nimbus_runtime` because that is where the kernel
invariants live.

## Choosing the cheapest useful test

Prefer the smallest test that can catch the bug class:

| Bug class | Cheapest useful test |
| --- | --- |
| Bad input validation | Unit or property test |
| Wrong status code | Route test |
| Adapter leaks HTTP exception | Integration test |
| Provider SDK exception maps wrong | Unit test with SDK stub |
| Duplicate request executes twice | Route test now, DST later |
| Crash after provider side effect | DST or focused recovery test |
| Model prompt injection bypass | Golden safety fixture plus runtime policy test |
| Replay mismatch | Projection property test |

## Verification loop

For docs-only changes:

```shell
uv run sphinx-build docs/source docs/build/html
```

For Python changes:

```shell
uv run ruff check --fix <touched paths>
uv run ruff format <touched paths>
uv run pytest <targeted tests>
```

For runtime or public-contract changes, add broader checks:

```shell
uv run pytest src/
uv run mypy --strict .
```
