# Cross-cutting Themes

A handful of ideas show up in nearly every lecture, every homework rubric, and
every code review for this course. They are the "why" behind decisions that
otherwise look arbitrary in the codebase. Each section below names the theme,
where it appears in the lectures and homeworks, and how it manifests in this
repository.

## 1. Interface / implementation split with DI registration

**Where it appears.** L05.1 (Building Quality In, on testability), L07.2
(Architecture and Design), L08.1 (Refactoring to a Bridge), HW1 (the entire
assignment), HW2 (the adapter is a third implementation of the same ABC),
HW3 (the AI client repeats the same pattern, plus a *shared* per-vertical
ABC).

**The shape.** A `*_api` package defines an `ABC` and a `get_client()`
factory. One or more `*_impl` packages each define a concrete subclass that
**registers itself with the factory on import**. Consumers depend only on
`*_api`. Implementations are swappable at deployment time without changing
caller code.

**In this repository.** `cloud_storage_api` is the abstract contract;
`aws_s3_storage_impl` and `cloud_storage_adapter` both register themselves
through `get_client()`. The same pattern repeats one level up for AI:
`ai_client_api` is the abstract contract and `openrouter_ai_client_impl` is
the registered implementation.

## 2. Location transparency — "where the code runs is geography"

**Where it appears.** L08.1 (Refactoring to a Bridge), HW2 in its entirety,
HW3 (cross-vertical integration).

**The shape.** Once an interface exists, the *location* of its
implementation is a deployment choice, not a design choice. A library call
and a remote HTTP call should be indistinguishable to the caller. The HW2
"three bridges" — service, generated client, adapter — exist so that the
adapter satisfies the same `ABC` the original library satisfied.

**In this repository.** Code that depends on `cloud_storage_api` does not
know whether the configured implementation is the local AWS S3 client or
the HTTP adapter that talks to the deployed Fly.io service. The HW3
cross-vertical work pulls another team's `*_api` as a `uv` git dependency
and integrates it the same way.

## 3. Test pyramid, FIRST, and AAA

**Where it appears.** L01 (Modern SE), L05.1 (Building Quality In). HW1
mandates three layers (unit / integration / end-to-end). HW2 reiterates them
and explicitly flags fully-mocked integration tests as an anti-pattern.

**The shape.** Many small unit tests, fewer mid-size integration tests, a
small number of end-to-end tests. Tests must be **F**ast, **I**solated,
**R**epeatable, **S**elf-verifying, **T**imely. Each test follows
**A**rrange–**A**ct–**A**ssert. Tests assert on **state and the public
API**, never on which method was called.

**In this repository.** Each component has a `tests/` directory with no
`__init__.py` (so production code can never accidentally import a test).
Property-based tests (Hypothesis) live alongside parametrized cases. The
testing playbook in `docs/source/testing-*.md` follows the same vocabulary.

## 4. Assignment-flow branching

**Where it appears.** L04.2 (Source Code Management). HW0 sets it up and
every later HW depends on it.

**The shape.** A protected `main`. One branch per homework
(`hw-0`, `hw-1`, `hw-2`, `hw-3`). PRs from `hw-N` to `main` are the
submission artifact and are **never merged**. Local feature work happens
on `feature-<name>` branches off the assignment branch. This branch
(`feature-class-reference-docs`) is an example of the latter.

**Why the rule.** Reviewers (TAs and peers) need a single stable diff per
homework. Merging would erase the comparison.

## 5. Public CI as part of the grade

**Where it appears.** L01, L11.1, L13.1, every homework rubric.

**The shape.** CircleCI runs lint (`ruff` with `select = ["ALL"]`),
type-checking (`mypy --strict`), the full `pytest` suite, and `coverage`.
The build is configured **public** so reviewers and graders can read it
without VCS credentials. Coverage reports must be reachable from the
CircleCI UI.

**In this repository.** `.circleci/config.yml` runs all of the above and
publishes the coverage HTML. The shape of the workflow lines up with the
HW3 telemetry expectations — CI itself is observable.

## 6. Observability — four golden signals

**Where it appears.** L11.2 (Seeing the Code), L12.1 (When Things Go
Wrong), HW3.

**The shape.** Three pillars: **metrics, traces, logs**. Four golden
signals: **latency, traffic, errors, saturation**. Service health is
expressed as **SLI / SLO / SLA**. HW3 narrows this down to a non-negotiable
minimum: latency, success rate, failure rate, visualized on a dashboard.

**In this repository.** OpenTelemetry instrumentation feeds a New Relic
dashboard. The dashboard surfaces request latency and the success/failure
split for the storage service and the AI server. See
{doc}`../deployment-operations` for the operational view.

## 7. Infrastructure as Code

**Where it appears.** L11.1 (Public Cloud), L13.1 (IaC Deep Dive), HW3.

**The shape.** Every piece of infrastructure — servers, containers,
databases, environment variables — is described declaratively, kept in
version control, and rolled out by a pipeline. Terraform is the
recommended tool; AWS CloudFormation and CDK are accepted alternatives.

**In this repository.** Fly.io app configuration is checked in as code;
secrets are managed by the platform's secret manager rather than hardcoded
or committed. Storage and Nimbus deploy through CircleCI on push to `hw-3`.

## 8. AI integration with tool calling

**Where it appears.** L14.1 (Future of Software Engineering), HW3.

**The shape.** Adding an AI client is not enough on its own. The
implementation must support **tool calling** — the model invoking domain
actions through a structured interface — and a unified credentials story
across the vertical. The pattern is the same DI/registration shape as the
storage client: `ai_client_api` plus a registered `*_ai_client_impl`.

**In this repository.** The Nimbus chat runtime registers a tool layer
that lets the model call into the cloud-storage API by name. See
{doc}`../ai-client-overview` and {doc}`../nimbus/index`.

## 9. Documentation as deliverable

**Where it appears.** Every homework rubric. HW3 makes it explicit:
*"Documentation is not optional."*

**The shape.** A buildable docs site (MkDocs in the syllabus, Sphinx with
MyST in this repository), per-component READMEs, a root README, a
`CONTRIBUTING.md`, and a `DESIGN.md`. The HW2 `DESIGN.md` adds a
request-flow diagram and an explicit adapter-pattern rationale.

**In this repository.** The Sphinx site at `docs/source/` is mounted at
`/guide` by the FastAPI app, so the live service serves its own
documentation. This page is part of that site.

## 10. Iterative submission

**Where it appears.** Every homework, the syllabus.

**The shape.** Every assignment is **build → review → iterate → final
submit**. The first draft is ungraded but skipping it forfeits the
feedback cycle. From HW2 onward, peer feedback must be addressed or have
written justification for being declined.

**In this repository.** The `hw-3` branch is structured as a sequence of
PRs, each addressing a distinct slice of the rubric, so reviewers can
read history rather than a single mega-diff.

## 11. "Why" before "how", tradeoffs over absolutes

**Where it appears.** L07.2 (Architecture and Design) explicitly. The
posture pervades every later lecture.

**The shape.** No technique is universally right. Architecture is a
spectrum, not a binary. Document the *why* of a decision so the next
contributor can re-evaluate when constraints change.

**In this repository.** `DESIGN.md` and the `docs/source/concepts/` pages
record decisions and their motivation. ADRs and design notes are favored
over implicit conventions.
