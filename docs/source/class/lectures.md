# Lectures

A condensed digest of the 19 lecture decks. Grouped by theme rather than by
date so related ideas sit together. Each entry lists the deck identifier
followed by the most useful concepts and any concrete tools or frameworks
the deck names.

The local `.mp4` files in the lecture directory were not summarized here.
For the file-by-file coverage inventory, see {doc}`source-materials`.

## Lecture-to-homework map

| Course thread | Lecture sources | Homework pressure |
|---|---|---|
| Component contracts and DI | L01, L05.1, L07.2 | HW1 requires a small provider-agnostic interface, a concrete implementation, and import-time DI registration. |
| Public review workflow | L03.1, L04.2, L10.1 | Every homework is a PR review cycle; branch discipline and meaningful feedback are graded work. |
| Testing as engineering design | L05.1, L10.2 | HW1 starts the unit/integration/E2E pattern; HW2 and HW3 raise the bar at adapter and system boundaries. |
| Service extraction and adapters | L06.0, L08.1, L07.2 | HW2 turns the library into a service, generated client, and adapter while preserving the public contract. |
| Customers, planning, and scope | L08.2, L09.1 | HW3 requires negotiation with other verticals, clear API contracts, and scoped integration slices. |
| Cloud, operations, and IaC | L11.1, L11.2, L12.1, L13.1 | HW3 must be deployed, managed as code, and observable through latency, success-rate, and failure-rate telemetry. |
| AI-assisted engineering | L14.1, L15.1 | HW3 uses AI as an integration surface with tool calling, while the course still expects strict validation and measurement. |

## Intro and onboarding

### L0 — Intro

- Instructor bios: Yotov (HFT, Two Sigma, Meta, HRT), Avteniev (LinkedIn,
  Stripe, StubHub, AWS).
- The three-part lecture format: core topic, war story, Q&A.
- Product-lifecycle overview and a taxonomy of engineering / product /
  design / data roles.

### L01 — Modern Software Engineering Introduction

- Historical arc: 1980s OOP → 1990s SCRUM/XP → 2000s Agile Manifesto and
  TDD → 2010s DevOps, microservices, cloud, IaC → 2020s developer
  experience and AI.
- Practices that compound: whole-team responsibility, short releases,
  observability, customer collaboration, the test pyramid, code review,
  pair / ensemble programming, refactoring, CI/CD.
- Definition framing: *"Software engineering is programming integrated
  over time."*

## People and teams

### L02.1 — Engineering Hiring

- Yotov's "insider perspective": the cost of bad hires is enormous,
  which is why interviews look the way they do.
- The hiring funnel: headcount → JD → pipeline → screen → onsite →
  decision → offer → onboarding → retention → separation.
- Interview prep advice: turn off IDE intelligence, use pen and paper,
  pick one language and stick to it.
- Avoid the cold-start problem by building a referral list and an
  expressed-interest list early.

### L03.1 — Teamwork

- **Cynefin framework** for context classification (simple, complicated,
  complex, chaotic).
- **Tuckman stages** of team formation: Forming → Storming → Norming →
  Performing.
- Agile and Scrum role schemes.
- The Spotify Squad / Tribe / Chapter / Guild model — and the candid
  acknowledgement that Spotify never fully realized it.
- LinkedIn's actual organizational structure as a contrast.
- Recommended class-team size: 3–4 members.

## Code and quality

### L04.2 — Source Code Management

- Centralized vs distributed VCS, framed by Microsoft's migration story.
- Microsoft monorepo statistics.
- Granularity options: company / org / language / platform / project.
- Ownership models: strong / weak / collective.
- Branching models:
  - **git-flow** for versioned products.
  - **GitHub Flow** for continuous deployment.
  - **Trunk-based development** as the recommended modern default.
- Modern code review: ~300 LOC/hour, reviews under 90 minutes most
  effective. Empirically, fewer than 5% of reviews find defects — the
  primary value is improving code, not catching bugs.
- The course's own model: **assignment-flow** — `hw-N` branches, PRs to a
  protected `main`, never merged.

### L05.1 — Building Quality In

- *Quality is everyone's job, not QA's.*
- Test dimensions:
  - **Size:** small / medium / large.
  - **Scope:** unit / integration / end-to-end.
- The **Test Pyramid** — and its anti-pattern, the ice-cream cone.
- TDD: Red → Green → Refactor. Granularity and uniformity matter more
  than the test-first / test-last debate.
- Empirical results from Microsoft and IBM TDD studies: lower defect
  density, 15–35% slower in absolute time.
- 80–100% coverage continues to reduce defects.
- **FIRST properties:** Fast, Isolated, Repeatable, Self-verifying,
  Timely.
- **AAA pattern:** Arrange, Act, Assert.
- Test the public API. Test state, not method invocation. No logic in
  tests.
- LLMs in testing: **Promptfoo** for testing LLMs; Meta uses LLMs for
  test automation.

### L06.0 — Refactoring

- Fowler's definition: change structure without changing behavior.
- Code smells: duplicate code, long methods, large components.
- Patterns: Move Method, Extract Class, Extract Method/Function.
- Empirical evidence is mixed — refactoring "may" improve coupling,
  cohesion, and complexity.

### L08.1 — Refactoring to a Bridge

- A concrete case study: extracting a pricing component from a monolithic
  ticketing webapp.
- Drivers: SOX compliance, pricing flexibility, deployment safety.
- Step-by-step extraction:
  1. Introduce an API.
  2. Fix DI.
  3. Extract the service (the Pricing Service).
  4. Extract the data.
  5. Inject an ML model at runtime.
- Maps directly onto the HW2 mechanics in this course.

## Architecture

### L07.2 — Role of Architecture and Design in Software Engineering

- **Architecture** is strategic, long-term, high-effort.
- **Design** is tactical, short-term.
- Reference example: Facebook TAO.
- "Laws":
  - Everything is a tradeoff.
  - "Why" before "how".
  - It's a spectrum, not a binary.
- Tension with Agile (which is documentation-light) is reconciled via
  iterative architecture, reference architectures, and evolutionary
  architecture.
- LinkedIn's RFC lifecycle is presented as a working example.
- Espresso (LinkedIn's storage system) is used as an architecture case
  study.

## Customers and planning

### L08.2 — Customers and Users

- Internal vs external customers; the PM as a customer proxy.
- User-Centered Design techniques: personas, scenarios, prototypes.
- AUCDI — Agile + UCD integration challenges.
- Culture statements at LinkedIn, Stripe, and Amazon ("customer
  obsession").
- Customers at LinkedIn: PMs, UX, Global Customer Service, A/B testing.

### L09.1 — Planning and Estimating

- The PM triangle: scope / cost / schedule.
- The CHAOS Report failure modes.
- User stories.
- **Planning poker** with **story points** as a relative-sizing technique.
- Accuracy metrics: **MMRE** and **Pred(x)**.
- LinkedIn's planning approach, end to end.

## Process and reflection

### L10.1 — Iterating on Process

- **Reflection** is individual.
- **Retrospective** is team-level.
- **Postmortem / learning review** is post-release, lasts hours, and no
  engineering work is allowed during it.
- Levels of reflection.
- A retrospective for the class itself happens during this lecture.

### L10.2 — Putting It All Together

- Case study: LinkedIn Live launch (February 2019, scale exploded during
  COVID).
- Product vision; engagement-feature design; architecture (race
  conditions, ownership boundaries); team workflows; the runup to launch.

## Cloud, observability, and IaC

### L11.1 — Building on the Public Cloud

- The IaaS / PaaS / FaaS / SaaS spectrum.
- On-premise vs cloud tradeoffs.
- Mapping the Agile Manifesto onto cloud capabilities.
- PaaS affordances: rapid elasticity, abstraction.
- Challenges: security, vendor lock-in, cost.
- Introduction to IaC and Platform Engineering.

### L11.2 — Seeing the Code (Observability)

- The ELK Stack.
- Distributed tracing across timeline / dependency / aggregation / RCA /
  anomaly use cases.
- **SLI / SLO / SLA** definitions.
- "The nines" — two, three, four nines uptime budgets.
- The Google SRE Book.
- Cloud monitoring requirements: scalable, cloud-aware, fault-tolerant,
  autonomic, comprehensive, time-sensitive.
- LinkedIn's stack: 5.3M metrics/sec, AutoMetrics, InGraph.
- Definition: *observability = inferring internal state from external
  output.*
- Three pillars: metrics, tracing, logs.

### L12.1 — When Things Go Wrong

- 2015 vs 2025 AWS outages compared.
- **The four golden signals: Latency, Traffic, Errors, Saturation.**
- On-call engineer responsibilities.
- Incident-management roles: Incident Lead, Ops Lead, Communications,
  Planning.
- The STELLA report and the SNAFU anatomy (Apache, Travis CI, Logstash
  case studies).
- Postmortems: blameless, ROI on anomalies, "dark debt".
- LinkedIn: most engineers on-call, integrated alerting, "Nurse"
  auto-remediation.

### L13.1 — IaC Deep Dive

- **Terraform**:
  - CLI driven; BSL licensed since August 2023; **OpenTofu** is the open
    fork.
  - Concepts: providers, resources, dependencies (`depends_on`), state
    (`terraform.tfstate`).
  - Key files: `terraform.tf`, `main.tf`, `variables.tf`, `outputs.tf`,
    `.terraform.lock.hcl`.
  - Commands: `init`, `fmt`, `validate`, `import`, `apply`, `state list`.
  - Remote state via HCP Terraform or backends such as GCS.
  - Concrete example: a `render_web_service` resource with an image
    source.
  - CircleCI integration patterns.
- **AWS CloudFormation**:
  - Templates, stacks, change-sets.
  - YAML or JSON.
  - Plugin architecture.
- **AWS CDK**:
  - IaC written in TypeScript, Python, or other languages.
  - Layered on top of CloudFormation.
  - Unit-testable.

## Future and measurement

### L14.1 — Future of Software Engineering

- ML in software engineering: data-center optimization, test
  prioritization.
- LLMs for code generation, completion, comprehension, and repair.
- Studies cited:
  - GitHub Copilot — 55% time-to-completion improvement on a narrow task,
    mixed in real settings.
  - CMU NL2Code — no improvement.
  - IBM watsonx Code Assistant — "junior developer" framing, deskilling
    concerns.
  - Cursor Pro OSS experiment — 19% **slower** with AI despite predicted
    speedup.
  - Three RCTs (Copilot at Microsoft, Accenture, a Fortune 100; n=4867)
    showed significant productivity gains.
- State of practice: Copilot at Stripe, Claude Code at StubHub
  (replaced an L4 engineer), Q / Kiro at AWS.
- Emerging "AI Engineering" specialization.
- Takeaway: AI assistants are now standard; you still need to
  understand the code; the "renaissance developer" decides what to
  build, validates correctness, and integrates.

### L15.1 — Measuring Software Development

- Why measure: understand, control, improve.
- Metric categories: progress (burn-down), process (CI pulse), quality
  (fault counts).
- Industry survey: 102 metrics observed in the wild; velocity and effort
  estimate are the most common; 39% of metrics are not in the literature.
- **DORA metrics:** Delivery Lead Time, Deployment Frequency, Time to
  Restore, Change Fail Rate.
- **SPACE Framework:** Satisfaction, Performance, Activity,
  Communication, Efficiency.
