# Nimbus Documentation

Release 0.1.0

Nimbus is the HW3 cloud-storage and AI platform in this repository. It combines
a provider-agnostic object-storage contract, an AWS S3 implementation, an HTTP
service and generated adapter, a provider-agnostic AI client contract, an
OpenRouter implementation, and a shared chat runtime that can be reached from
the CLI or an HTTP wrapper.

The docs are organized around the way contributors actually work: get a local
demo running, trace the architecture from the public boundary inward, then use
the reference pages when you need exact API, endpoint, testing, or operations
details.

## Quick demo

Install the workspace and run the fast tests:

```shell
uv sync --all-packages
uv run pytest src/ -q
```

Start the combined FastAPI app. It serves the storage API at the root, the AI
server under `/ai`, and these Sphinx docs under `/guide` after you build them.

```shell
uv run sphinx-build docs/source docs/build/html
SESSION_SECRET_KEY=dev-session-secret \
API_KEY=dev-storage-key \
AI_SERVER_API_KEY=dev-ai-key \
AI_SERVER_SIGNING_SECRET=dev-signing-secret \
uv run uvicorn aws_client_service.main:app --reload
```

Run Nimbus from the terminal after setting `OPENROUTER_API_KEY`:

```shell
uv run --package openrouter-ai-client-impl nimbus
```

## Start here

- {doc}`getting-started` - the first 20 minutes: install, test, run services,
  try the storage API, and start Nimbus.
- {doc}`developer-guide` - the complete contributor/developer path for running
  docs, tests, services, and building new packages or integrations.
- {doc}`cloud-storage/index` - the storage product manual: concepts, Python SDK
  usage, HTTP API examples, adapter behavior, generated client rules, and error
  semantics.
- {doc}`architecture-overview` - the HW3 system map: storage vertical,
  AI/runtime vertical, transport boundaries, state ownership, and failure
  modes.
- {doc}`complete-system-design` - the deep system-design teardown: public
  contracts, invariants, bottlenecks, failure modes, and review questions.
- {doc}`CONTRIBUTING` - how to become a user, trace hot paths, make bite-sized
  changes, and keep PRs reviewable in this codebase.
- {doc}`api` - endpoint reference for the storage service and the AI wrapper
  routes.

## Guides

- {doc}`cloud-storage/index` - everything about the cloud storage vertical.
- {doc}`nimbus/index` - focused guides for the AI service, signed wrapper
  contract, sessions, attachments, and smoke tests.
- {doc}`concepts/index` - a glossary and reliability field guide for the
  storage, AI, runtime, testing, and agent-platform concepts used here.
- {doc}`deployment-operations` - Render deployment, Postgres state,
  environment variables, health checks, telemetry, and rollback notes.
- {doc}`testing` - pytest basics through property, fuzz, integration, and e2e
  workflows.
- {doc}`doctest-examples` - examples kept small enough to execute as docs tests.

## Reference

- {doc}`reference/python-api` - autodoc reference for the hand-authored Python
  packages.
- {doc}`api` - HTTP endpoints and response shapes.
- {doc}`DESIGN` - storage service design history and compatibility notes.
- {doc}`ai-client-api` - AI contract and OpenRouter implementation reference.
- {doc}`class/index` - course context: syllabus, homework rubrics, lecture
  digest, and the cross-cutting themes that shape this codebase.

```{toctree}
:hidden:
:caption: Getting Started
:maxdepth: 2

getting-started
developer-guide
architecture-overview
complete-system-design
CONTRIBUTING
```

```{toctree}
:hidden:
:caption: Cloud Storage
:maxdepth: 2

cloud-storage/index
```

```{toctree}
:hidden:
:caption: Nimbus and AI
:maxdepth: 2

nimbus/index
ai-client-overview
ai-client-tutorial
ai-client-guardrails
ai-client-api
nimbus-ai-service
```

```{toctree}
:hidden:
:caption: Concepts and Reliability
:maxdepth: 2

concepts/index
```

```{toctree}
:hidden:
:caption: Operations
:maxdepth: 2

deployment-operations
```

```{toctree}
:hidden:
:caption: Testing
:maxdepth: 2

testing
doctest-examples
```

```{toctree}
:hidden:
:caption: Reference
:maxdepth: 2

api
reference/python-api
DESIGN
```

```{toctree}
:hidden:
:caption: Class Reference
:maxdepth: 2

class/index
```
