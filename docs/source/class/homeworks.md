# Homeworks

The course is one continuous engineering project broken into iterative
"sprints." Teams of 5–6 students build and refactor the same vertical across
four homeworks. Verticals are **Calendar, Chat, Cloud Storage, and Issue
Tracker**. This repository is the **Cloud Storage** vertical.

All homeworks share the same iteration loop: **Build (first draft) → Peer +
TA Review → Iterate → Final submission (graded)**. The first draft is
ungraded but skipping it forfeits the feedback cycle.

## HW0 — Course Setup and Team Formation

**Goal:** stand up the team and the repository so future assignments have a
place to land.

Required deliverables:

- A team of up to six members, finalized before the second class. Students
  without a team sign up as a "Free Agent" on the Team Formation Sheet.
- A team GitHub repository with collaborators added — including the three
  TAs by handle (`adithyab-20`, `ivanearisty`, `AranyaAryaman`).
- PR template (designed thoughtfully — every assignment is a PR).
- Bug and feature issue templates.
- Python `.gitignore`.
- An OSI license (MIT or Apache 2.0).
- A basic `README.md`.
- Submission of the repo link on Brightspace and the Team Formation Sheet.

## HW1 — Building a Client using Components

**Goal:** design a clean abstract interface and a concrete implementation
following dependency-injection patterns. This becomes the foundation for the
rest of the course.

### Components

Two installable packages are required:

1. **`[vertical]_client_api`** — the abstract interface.
   - Defined as an `ABC` in a `.py` file (not `__init__.py`).
   - Exposes an abstract `get_client()` factory.
   - Must have **zero dependencies** on implementation details, vendor SDKs,
     HTTP clients, or auth tokens.
2. **`[vertical]_client_impl`** — a concrete subclass.
   - Auto-registers itself with the interface's factory on import (this is
     the dependency-injection wiring).
   - Reads credentials only from environment variables.
   - Implements appropriate auth (OAuth 2.0, API keys, etc.).

### Required tests

Each component must ship with three test layers under its own `tests/`:

- **Unit** — mocked APIs.
- **Integration** — verifies DI wiring (importing the impl registers it).
- **End-to-end** — runs against a real provider with test credentials.

A coverage threshold is enforced. `# pragma: no cover` is allowed only on
lines that are intentionally untestable.

### CI and docs

- CircleCI runs lint, types, and the full test suite.
- The Tests dashboard and a browsable coverage report must be reachable from
  the public CircleCI UI.
- Required documents: root README, per-component READMEs, `contributing.md`,
  `design.md`, and a buildable docs site.

### Recommended reading

*A Philosophy of Software Design* by John Ousterhout — the book argues for
"deep" interfaces (a small surface that hides a lot of functionality), which
is the exact criterion HW1 grades against.

### Rubric (100 + 10 EC)

| Area              | Points |
| ----------------- | ------ |
| Repo & Process    | 15     |
| Tooling           | 15     |
| Interface         | 15     |
| Implementation    | 15     |
| Testing           | 20     |
| CI                | 10     |
| Docs              | 10     |
| Peer Review       | 10     |
| **Extra credit**  | +10    |

Detailed grading signals from the rubric:

- **Repository & Process (15):** correct `hw-1` → `main` PR that is not
  merged; clean `src/` or `components/` package layout; `.gitignore` covers
  virtualenvs, bytecode, credentials, `.env`, and build artifacts; small
  imperative commits; useful PR and issue templates.
- **Tooling & Configuration (15):** `uv` workspace, no `pip` or
  `requirements.txt`; all `ruff`, `mypy`, `pytest`, and coverage config in
  root `pyproject.toml`; `ruff` uses `select = ["ALL"]`; `mypy` is strict;
  absolute imports only, no `__all__`, and no `__init__.py` in test
  directories.
- **Interface Component (15):** ABC contract in a dedicated module; no
  implementation, framework, SDK, auth-token, or provider-type leakage; a
  factory such as `get_client()` establishes the DI seam.
- **Implementation Component (15):** concrete class inherits the interface,
  implements every abstract method, registers itself on import, and handles
  provider auth without hardcoded credentials.
- **Testing Strategy (20):** unit tests per component with mocked provider
  APIs; integration tests prove DI wiring; E2E tests exercise real
  infrastructure with test credentials; coverage threshold is configured;
  CircleCI test results are visible.
- **CI/CD (10):** CircleCI runs lint, type checks, and all tests; the `hw-1`
  branch is passing; CI is public; coverage HTML is browsable from CircleCI.
- **Documentation (10):** root README explains purpose, architecture, setup,
  auth, and commands; component READMEs explain API/dependencies/role;
  contributing and design docs exist; the docs site builds.
- **Peer Review (10):** five points for substantive review comments on the
  assigned PR, and five points for contributor/design docs that let an
  outside contributor understand the project.

Extra credit is awarded for notably deep interface design, typed domain
exceptions, and resilience patterns such as retries, rate-limit handling, and
idempotency.

### Note on peer review

For HW1 only, peer-review feedback received does **not** have to be
addressed — only TA feedback does. From HW2 onward, peer feedback must be
addressed or have a written justification for being declined.

The HW1 review guide asks reviewers to focus on interface quality: whether the
contract leaks implementation details, whether a second provider could
implement it cleanly, whether the interface is "deep" enough to justify its
surface area, and whether DI is wired correctly. Review quality is graded by
care and insight, not comment volume.

## HW2 — Refactor the library into a service ("the three bridges")

**Goal:** convert the HW1 library into a deployed HTTP service so that
consumers no longer need to import the implementation. The same consumer
code must work whether the backend is a local library or a remote service.
Prof. Yotov's framing: *"where the code runs is merely geography."* This is
**location transparency**.

### The three bridges

1. **`[vertical]_service`** — a FastAPI wrapper around the existing
   implementation.
   - Imports the impl directly; does **not** reimplement business logic.
   - Endpoints mirror the abstract API.
   - **OAuth 2.0 Authorization Code Flow** is implemented at
     `/auth/login` and `/auth/callback` (redirect → callback → token
     exchange → session storage).
   - `/health` returns 200.
   - Domain exceptions are translated into proper HTTP statuses; raw 500s
     are not allowed.
2. **`[vertical]_service_api_client`** — auto-generated from the service's
   `/openapi.json` using
   [`openapi-python-client`](https://github.com/openapi-generators/openapi-python-client).
   The adapter must use this client; hand-rolled `requests` or `httpx`
   calls are explicitly forbidden.
3. **`[vertical]_adapter`** — implements the original abstract API by
   delegating to the auto-generated client.
   - Translates HTTP errors back into the domain exceptions defined in
     `_api`.
   - Contains no business logic.
   - Importing it registers it via `get_client()` so consumer code is
     unchanged.

### Domain modeling

- Ports-and-adapters philosophy.
- `pydantic` is used **only at the edges** (HTTP boundary and adapter
  layer).
- The domain itself uses `dataclass` and `ABC`.
- Typed domain exceptions, never `None` for control flow.

### Deployment

- The service is deployed to a public cloud.
- `/openapi.json` and `/health` must be reachable.
- CircleCI auto-deploys on push to `hw-2`.
- Secrets are managed via the platform's native secret manager.

### Required `DESIGN.md`

The HW2 `DESIGN.md` must cover:

- Architecture overview.
- A request-flow diagram: user code → adapter → generated client →
  FastAPI → impl.
- API design with error handling.
- Adapter-pattern rationale and a code comparison.
- Testing strategy.

### Rubric (100 + 10 EC)

| Area               | Points |
| ------------------ | ------ |
| Repo & Process     | 15     |
| Tooling            | 15     |
| New Components     | 20     |
| Domain Modeling    | 8      |
| Testing            | 18     |
| CI/CD/Deploy       | 10     |
| Docs               | 7      |
| Peer Review        | 10     |
| `DESIGN.md`        | 7      |
| **Extra credit**   | +10    |

Detailed grading signals from the rubric:

- **Repository & Process (15):** correct `hw-2` → `main` PR, not merged;
  all installable packages, including the generated client, are workspace
  members; HW1 templates and `.gitignore` hygiene are preserved; commits are
  small, logical, and written for squash-merge review.
- **Tooling & Configuration (15):** every `src/*` package is listed in the
  `uv` workspace; central config remains in root `pyproject.toml`; strict
  `ruff`/`mypy` and absolute imports are preserved; runtime dependencies stay
  minimal and local to the component that needs them.
- **New Components (20):** the five-package shape is clear:
  `[vertical]_api`, `[vertical]_impl`, `[vertical]_service`,
  `[vertical]_service_api_client`, and `[vertical]_adapter`. The service
  delegates to the implementation over HTTP; the generated client is produced
  from `/openapi.json`; the adapter maps generated types back to the original
  interface without business logic.
- **Domain Modeling & API Design (8):** domain packages stay framework-free;
  Pydantic is only at HTTP/adapter edges; auth tokens, HTTP clients, and SDK
  types do not leak into ports; typed domain exceptions are translated at both
  adapter boundaries.
- **Testing Strategy (18):** unit tests cover public behavior with fakes or
  mocks for external services; integration tests validate DI and adapter
  mapping without mocking the whole world; E2E tests run a black-box entry
  point and demonstrate location transparency; coverage gates CI.
- **CI/CD & Deployment (10):** CircleCI runs lint, typing, and all test
  layers; `/openapi.json` and `/health` are reachable on a public cloud
  service; deployment is automatic on `hw-2`; secrets live in the platform's
  secret manager.
- **Documentation (7):** README covers ports/adapters architecture, setup,
  auth, deployment, env vars, and commands; every package README is current;
  the docs site builds and includes HW2 navigation.
- **Peer Review (10):** five points for giving useful review, five for
  addressing received feedback or recording a rationale for declining it.
- **`DESIGN.md` (7):** covers architecture, request flow, API/error design,
  adapter-pattern rationale with code comparison, and testing strategy.

Extra credit is awarded for Docker containerization and multi-user OAuth
session isolation.

### Anti-pattern call-out

*Integration tests that mock all components* are explicitly flagged as an
anti-pattern in the assignment. Integration tests that fully mock the world
do not validate wiring and are considered worthless.

## HW3 — Intelligent Application: AI + Cross-vertical + IaC + Observability

There are three HW3 source files in the local materials:

- `OSPSD Spring _26 - HW3.docx` — the **issued** editable version with firm
  dates.
- `OSPSD Spring '26 - HW3.docx.pdf` — a PDF export of the issued version.
- `[DRAFT] hw3-assignment.pdf` — an earlier draft with TBD deadlines.

The issued DOCX and exported PDF agree on the gradeable requirements.
Differences from the earlier draft are listed at the end of this section.

No standalone HW3 rubric file was present in `books/class/Homeworks`; the
gradeable signals are embedded in the issued assignment itself.

### Dates (issued version)

| Milestone           | Date       |
| ------------------- | ---------- |
| HW3 released        | 2026-04-04 |
| First submission    | 2026-04-10 |
| Second submission   | 2026-04-22 |
| Peer / TA reviews   | 2026-05-01 |
| Final submission    | 2026-05-13 |

### Vertical groupings (course-wide)

| Vertical       | Teams        |
| -------------- | ------------ |
| Chat           | 4, 8, 9      |
| Issue Tracker  | 1, 3, 7      |
| Cloud Storage  | 2, 6, 10     |
| Calendar       | 5, 11, 12    |

This repository is **Cloud Storage**, alongside teams 6 and 10.

### Core steps

1. **Shared per-vertical interface.**
   Teams within the same vertical jointly design a Python `ABC` and publish
   it as a separate Git repository. Each team consumes it via
   `uv add git+https://github.com/[your-org]/[vertical]-api`.
   - One representative per vertical submits a memo.
   - Each team submits its own plan-of-action.
   - The grade for the shared API is **shared across the entire vertical**.
   - The memo **must not** be drafted with AI.

2. **AI integration.**
   Every team adds an `ai_client_api` plus a concrete
   `[provider]_ai_client_impl` (OpenAI, Claude, or Gemini).
   - Must support **tool calling** wired to domain actions, not just
     chat.
   - The assignment suggests a minimal
     `send_message(prompt: str, context: dict[str, Any] | None = None) -> str`
     shape, but the important requirement is a provider-agnostic contract
     plus concrete implementation.
   - The vertical agrees on a unified credentials approach.

3. **Cross-vertical integration.**
   Pull at least one other vertical's shared API as a dependency and
   integrate it (for example, the Chat team uses the Issue Tracker team's
   API). The negotiation between teams is part of the assignment.

4. **Infrastructure as Code.**
   Terraform (recommended) or AWS CloudFormation provisions all
   infrastructure (servers, containers, databases, environment variables).
   The IaC code lives in version control with its own pipeline.

5. **Telemetry.**
   Non-negotiable. Monitor at least: request latency, success rate, and
   failure rate. Visualize on a dashboard.

6. **Final deliverable.**
   A clean PR plus a video demo covering: project explanation, live
   functionality (including provider swap), CircleCI pipeline walkthrough,
   cloud test explanation, end-to-end / integration test overview, and a
   tour of the telemetry dashboard.

### Suggested per-vertical APIs

- **Chat:** `get_message`, `send_message`, `delete_message`,
  `get_messages`, `get_channels`, and `get_channel`.
- **Issue Tracker:** `Ticket` dataclass, `get_ticket`, `create_ticket`,
  `get_tickets`, `update_ticket_status`.
- **Cloud Storage:** `upload_file`, `download_file`, `list_files`,
  `delete_file`, `get_file_info`.
- **Calendar:** `list_events`, `get_event`, `create_event`, `update_event`,
  `delete_event`.

For Cloud Storage specifically, the issued assignment names this repository's
vertical group as Team 2 (AWS S3), Team 6 (GCP Cloud Storage), and Team 10
(S3). The shared API has to hide provider-specific authentication,
bucket/container vocabulary, and metadata differences while keeping upload,
download, list, delete, and info operations stable.

### Suggested PR sequence (Chat example)

```
feat: Align to shared API
feat: Integrate AI client
feat: Integrate ticket system
fix: Address TA feedback
feat: Add e2e tests & main.py
```

### TA Iván's strict Pylance configuration

The HW3 remarks include a hyper-strict VS Code Pylance configuration —
`typeCheckingMode: strict`, all inlay hints on, strict list/set inference.
He clearly grades against this mindset.

### Differences between the issued files and the draft PDF

- The draft PDF has **TBD deadlines** and a "Todo" callout; the docx has
  the firm dates listed above. The exported issued PDF matches those dates.
- The issued files add an explicit preamble — *"Documentation is not
  optional"* — and number the core steps. The draft is more loosely worded.
- The issued files include the `uv` git-dependency guidance and the requirement
  to create a **shared per-vertical Git repository** of ABCs with each
  team as a contributor. This is **absent** from the draft.
- Both versions agree on vertical groupings, suggested method signatures,
  and the strict-Pylance "Remarks" section.

## How this repository maps onto the homeworks

This repository is the **Cloud Storage** vertical and accumulates HW1, HW2,
and HW3 deliverables on the `hw-3` branch.

- HW1 produced the abstract `cloud_storage_api` and an AWS S3
  implementation.
- HW2 added the `aws_client_service` FastAPI wrapper, the auto-generated
  `aws_client_service_api_client`, and the `cloud_storage_adapter` shim.
- HW3 added the `nimbus` chat runtime, the `openrouter_ai_client_impl`
  AI integration, IaC for Fly.io, and the telemetry dashboard.

For the live architecture map, see {doc}`../architecture-overview`. For the
storage product manual, see {doc}`../cloud-storage/index`. For the AI
service, see {doc}`../nimbus/index`.
