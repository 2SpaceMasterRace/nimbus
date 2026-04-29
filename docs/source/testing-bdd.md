# Behavior-Driven Acceptance Tests

BDD is the test layer for product promises that should be readable without
opening the implementation. In this repository, BDD means:

- Gherkin feature files under `tests/bdd/features/`
- Python step definitions under `tests/bdd/`
- the `pytest-bdd` plugin, so scenarios run inside the normal pytest fixture,
  marker, and assertion system
- deterministic local fakes, not live OpenRouter or live AWS calls

Use BDD sparingly. It is valuable when a scenario names a wrapper-facing
contract that product, integration, and testing people all care about. It is not
the right tool for every validation branch or every combination of parameters.

## What BDD Covers Today

The initial BDD suite encodes the Nimbus wrapper acceptance contract:

| Feature file | Promise |
| --- | --- |
| `chat_turn.feature` | Signed Slack-like messages return the canonical `reply` contract and can receive read-only storage tools. |
| `wrapper_signed_auth.feature` | Missing, tampered, and replayed signed requests fail closed with `401`. |
| `confirmation_flow.feature` | Destructive delete intent returns `confirmation_required`; only the same actor can confirm. |
| `attachment_ingestion.feature` | Inline attachment bytes can upload successfully, fail as `error`, or produce `partial_success`. |

Run them with:

```shell
uv run pytest tests/bdd -q --no-cov
```

or through the convenience task:

```shell
just bdd
```

The BDD file itself is marked with both `unit` and `bdd`, so these scenarios are
fast enough for the normal local and CI-safe suite.

## When To Add A BDD Scenario

Add a BDD scenario when all of these are true:

- The behavior is a user-visible or wrapper-visible product contract.
- The scenario can be stated in a short Given/When/Then flow.
- The expected result is stable enough to be living documentation.
- The scenario exercises real routing, auth, runtime, session, or storage-tool
  wiring with deterministic fakes.

Good BDD candidates:

- signed wrapper request -> `reply`
- destructive intent -> `confirmation_required` -> same-actor confirmation
- invalid signature -> `401`
- attachment upload with one success and one failure -> `partial_success`
- Slack mapping rule for top-level message vs thread reply

Poor BDD candidates:

- every individual Pydantic length limit
- every HMAC byte-digest permutation
- every private helper branch
- low-level storage adapter status-code mapping
- fuzz/property-style malformed input spaces

Those belong in unit, property, integration, or fuzz tests.

## File Layout

```text
tests/bdd/
├── test_wrapper_acceptance.py
└── features/
    ├── attachment_ingestion.feature
    ├── chat_turn.feature
    ├── confirmation_flow.feature
    └── wrapper_signed_auth.feature
```

Keep feature files small and product-shaped. Put reusable Python setup in the
step-definition module. If a feature grows past a handful of scenarios, split it
by product concept rather than by implementation module.

## Step Definition Rules

The step definitions deliberately reuse the real application boundary:

- FastAPI `TestClient`
- the real `ai_server.router`
- the real signed-request auth layer
- the real `nimbus_runtime`
- fake AI and storage clients injected through FastAPI dependency overrides

That gives the BDD suite more confidence than pure unit tests while keeping it
stable and credential-free.

When adding steps:

- Prefer business language in `.feature` files.
- Keep implementation details inside Python steps.
- Avoid overly generic steps such as "the system works".
- Avoid scenario outlines unless they make the acceptance rule clearer.
- Assert response payload fields directly in Python, not by parsing rendered text.
- Use deterministic event IDs, nonces, filenames, and remote paths.

## Example

Feature:

```gherkin
Scenario: Same actor can confirm a pending delete
  Given the wrapper signing secret is configured
  And a pending delete exists for "reports/2024/old.csv"
  And the wrapper sends a Slack message "yes, delete reports/2024/old.csv" with event id "evt-bdd-delete-confirm"
  When the wrapper posts the signed chat turn
  Then the response status is 200
  And the response outcome is "reply"
  And the storage client deleted "reports/2024/old.csv"
```

Step definitions use `pytest-bdd` decorators and ordinary pytest fixtures:

```python
from pytest_bdd import given, parsers, then, when


@given(parsers.parse('a pending delete exists for "{remote_path}"'))
def pending_delete_exists(client, bdd_context, *, remote_path: str) -> None:
    ...


@when("the wrapper posts the signed chat turn")
def wrapper_posts_signed_turn(client, bdd_context) -> None:
    ...


@then(parsers.parse('the storage client deleted "{remote_path}"'))
def storage_deleted_remote_path(fake_storage_client, *, remote_path: str) -> None:
    ...
```

See the current implementation in `tests/bdd/test_wrapper_acceptance.py`.

## Verification

For BDD-only changes:

```shell
uv run pytest tests/bdd -q --no-cov
uv run ruff check tests/bdd
uv run ruff format tests/bdd
```

Before finishing a broader wrapper-contract change:

```shell
uv run pytest tests/bdd src/ai_server/tests/test_wrapper_contract.py src/nimbus_runtime/tests/test_runtime.py -q --no-cov
uv run pytest src/ -q
uv run sphinx-build docs/source docs/build/html
```

The BDD layer should stay fast. If a scenario needs live credentials, deployed
URLs, or provider calls, it belongs in the e2e suite instead.
