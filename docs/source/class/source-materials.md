# Source Materials Covered

This page records the local class-material corpus used to build the class
reference docs. The source directory inspected was `books/class`.

The three `.mp4` files under `books/class/Lecture Slides/` were intentionally
excluded from summarization. `.DS_Store` was ignored as filesystem metadata.
Every PDF, DOCX, and PPTX file below was inspected and folded into
{doc}`syllabus`, {doc}`homeworks`, {doc}`lectures`, or {doc}`themes`.

## Syllabus

| Source file | Covered in docs |
|---|---|
| `Syllabus Spring 2026.pdf` | {doc}`syllabus`: course basics, lifecycle scope, objectives, grade weights, office hours, late policy, academic-integrity and inclusion policies. |

## Homework and Review Files

| Source file | Covered in docs |
|---|---|
| `Homeworks/OSPSD Spring _26 - HW0.docx` | {doc}`homeworks`: team formation, repository setup, PR/issue templates, license, README, and submission target. |
| `Homeworks/OSPSD Spring _26 - HW1.docx` | {doc}`homeworks`: component model, interface/implementation split, DI, test layers, CI, docs, process timeline, and tooling checklist. |
| `Homeworks/OSPSD Spring _26 - HW1 - Rubric.pdf` | {doc}`homeworks`: full HW1 100-point rubric plus extra-credit themes. |
| `Homeworks/Homework 1_ Review - Spring _26.docx` | {doc}`homeworks`: peer-review focus on interface depth, implementation leakage, and DI correctness. |
| `Homeworks/Homework 2_ Review - Spring _26.docx` | {doc}`homeworks`: DESIGN.md requirements, the service/generator/adapter bridge model, and peer-review expectations. |
| `Homeworks/OSPSD Spring _26 HW2 - Rubric.pdf` | {doc}`homeworks`: full HW2 100-point rubric plus extra-credit themes. |
| `Homeworks/OSPSD Spring _26 - HW3.docx` | {doc}`homeworks`: issued HW3 assignment, firm dates, vertical groupings, shared API, AI, cross-vertical, IaC, telemetry, final-video, and strict-tooling requirements. |
| `Homeworks/OSPSD Spring '26 - HW3.docx.pdf` | {doc}`homeworks`: PDF export of the issued HW3 assignment; confirms the same dates and deliverables as the DOCX. |
| `Homeworks/[DRAFT] hw3-assignment.pdf` | {doc}`homeworks`: compared against the issued HW3 document; retained only where it clarifies shared requirements. |

## Lecture Decks

| Source file | Covered in docs |
|---|---|
| `Lecture Slides/Lecture 0_ Intro.pptx` | {doc}`lectures`: lecture format, instructor/industry context, product-lifecycle framing, and engineering role taxonomy. |
| `Lecture Slides/Lecture 01_ Modern Software Engineering Introduction.pptx` | {doc}`lectures`: historical arc from OOP through AI-assisted development, core practices, and the long-term definition of software engineering. |
| `Lecture Slides/Lecture 02.1_ Engineering Hiring.pptx` | {doc}`lectures`: hiring funnel, interview preparation, referrals, and onboarding/retention model. |
| `Lecture Slides/Lecture 03.1_ Teamwork.pptx` | {doc}`lectures`: Cynefin, Tuckman stages, Scrum roles, Spotify model, and real organization tradeoffs. |
| `Lecture Slides/Lecture 04.2_ Source Code Management.pptx` | {doc}`lectures`: VCS models, monorepos, ownership, branching, review throughput, and assignment-flow. |
| `Lecture Slides/Lecture 05.1_ Building Quality In.pptx` | {doc}`lectures`: test pyramid, TDD, FIRST, AAA, coverage, and LLM testing. |
| `Lecture Slides/Lecture 06.0_ Refactoring.pptx` | {doc}`lectures`: behavior-preserving change, code smells, and Fowler refactorings. |
| `Lecture Slides/Lecture 07.2_ Role of Architecture and Design in Software Engineering.pptx` | {doc}`lectures`: architecture/design tradeoffs, evolutionary architecture, RFCs, and Espresso/TAO examples. |
| `Lecture Slides/Lecture 08.1_ Refactoring to a Bridge.pptx` | {doc}`lectures`: extracting a service from a monolith, DI repair, data extraction, and the HW2 bridge pattern. |
| `Lecture Slides/Lecture 08.2_ Customers and Users.pptx` | {doc}`lectures`: customers vs users, PM proxy role, personas, scenarios, UCD, and customer-obsession examples. |
| `Lecture Slides/Lecture 09.1_ Planning And Estimating.pptx` | {doc}`lectures`: PM triangle, CHAOS failure modes, stories, planning poker, MMRE, and Pred(x). |
| `Lecture Slides/Lecture 10.1_ Iterating on Process.pptx` | {doc}`lectures`: reflection, retrospectives, postmortems, and the class retrospective. |
| `Lecture Slides/Lecture 10.2_ Putting it all together.pptx` | {doc}`lectures`: LinkedIn Live case study across product, architecture, race conditions, and launch workflow. |
| `Lecture Slides/Lecture 11.1_ Building on the public cloud.pptx` | {doc}`lectures`: cloud service models, elasticity, abstraction, cost, security, lock-in, IaC, and platform engineering. |
| `Lecture Slides/Lecture 11.2_ Seeing the Code.pptx` | {doc}`lectures`: ELK, tracing, SLI/SLO/SLA, uptime budgets, SRE, and metrics/traces/logs. |
| `Lecture Slides/Lecture 12.1_ When Things Go Wrong.pptx` | {doc}`lectures`: AWS outage comparison, four golden signals, incident roles, STELLA/SNAFU, postmortems, and auto-remediation. |
| `Lecture Slides/Lecture 13.1_ IaC Deep Dive.pptx` | {doc}`lectures`: Terraform, OpenTofu, state, commands, backends, CircleCI integration, CloudFormation, and CDK. |
| `Lecture Slides/Lecture 14.1_ Future of Software Engineering.pptx` | {doc}`lectures`: ML/LLM-assisted engineering, Copilot/Cursor/Claude Code evidence, and AI engineering. |
| `Lecture Slides/Lecture 15.1_ Measuring Software Development.pptx` | {doc}`lectures`: progress/process/quality metrics, DORA, SPACE, and metric-selection tradeoffs. |

## Excluded Video Files

These files were found but intentionally not summarized:

- `Lecture Slides/effective-ci-cd.mp4`
- `Lecture Slides/pm101-zoelu-fa2023.mp4`
- `Lecture Slides/technical-decision-making-with-daniel-walt.mp4`
