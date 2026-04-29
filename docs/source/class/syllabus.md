# Syllabus and Course Standards

## Course basics

- **Course:** CS-GY 9223 / CS-UY 3943 — *Special Topics in CS: Open Source and
  Professional Software Development*.
- **Institution:** NYU Tandon, Spring 2026.
- **Format:** Wednesdays 6:30–9:00 PM, Jacobs Hall room 203. Combined
  graduate / undergraduate. Highly interactive; attendance is not taken but
  recommended.
- **Instructors:** Prof. Nikolai Avteniev (`na414@nyu.edu`) and Prof. Kamen
  Yotov (`ky12@nyu.edu`).
- **Teaching assistants:** Iván Aristy Eusebio, Adithya Balachandra,
  Aranya Aryaman.
- **Communication:** dedicated course Slack workspace, Google Drive Student
  Portal, submissions via Brightspace.

## Office hours

TA office hours are appointment-aware: message on Slack or email before
showing up.

| Staff member | Time |
|---|---|
| Iván Aristy Eusebio | Tuesdays 9:00–11:00 PM; PM on Slack if attending. |
| Adithya Balachandra | Fridays 2:00–4:00 PM; PM on Slack if attending. |
| Aranya Aryaman | Sundays 7:00–9:00 PM; email or Slack at least two hours ahead. |
| Professors Avteniev and Yotov | After lecture or by appointment. |

## Course scope and objectives

The syllabus frames the course as the parts of software development that
ordinary school projects usually skip but professional teams rely on:

- product discovery, requirements, proposals, interaction design, technical
  design, and design review;
- implementation, code review, testing, deployment, operation, maintenance,
  deprecation, and decommissioning;
- open-source contribution norms and corporate software-development practice;
- the different hats a software developer may wear in a large organization.

The explicit objective is not only to build a working project. It is to expose
students to the full software-development lifecycle, team roles, open-source
project work, industry stories, and the habits expected before a real job.

## Lecture structure

Each lecture is split into three parts:

1. A topic from the product lifecycle (engineering hiring, teamwork, source
   code management, quality, refactoring, architecture, planning, process
   iteration, public cloud, observability, IaC, future of SE, measurement).
2. Presentation of an open-source idea that feeds into the homework pipeline.
3. A "war story" from a guest industry veteran.

## Mandated technical stack

The course standardizes the tooling for every assignment. Variation is not
permitted unless the assignment explicitly says so.

- **Language and runtime:** Python only.
- **Package manager:** [`uv`](https://docs.astral.sh/uv/) — `pip`,
  `requirements.txt`, and ad-hoc virtualenv tooling are not allowed.
- **Project metadata:** `pyproject.toml` only.
- **Lint and format:** `ruff` configured with `select = ["ALL"]`. Each
  ignore must have a written justification.
- **Type checker:** `mypy --strict`. Random `# type: ignore` lines lose
  points.
- **Tests:** `pytest` with `coverage.py`.
- **Documentation:** MkDocs in the original syllabus. *This repository uses
  Sphinx with MyST*; the principle (a buildable docs site that reviewers can
  browse) is the same.
- **Continuous integration:** CircleCI, configured to be **public** so
  reviewers can see results and coverage reports.
- **Version control / collaboration:** GitHub with PR templates, issue
  templates, and peer review.
- **HW3-specific additions:** Terraform (or AWS CloudFormation) for
  Infrastructure as Code, an observability platform (Prometheus, Grafana, or
  similar), and an external AI provider (OpenAI, Anthropic Claude, or Google
  Gemini).

## Repository conventions

The course also fixes the *shape* of the repository.

- **Monorepo** with a `src/` (or `components/`) workspace; each component is
  an installable Python package.
- All lint, type, test, coverage, and docs configuration lives centrally in
  the root `pyproject.toml`.
- **Absolute imports only.** No relative imports.
- **No `__all__`** in `__init__.py`.
- **No `__init__.py` in `tests/`** so production code cannot import test
  modules.
- **Credentials via environment variables.** Never hardcoded, never in
  source control, never inside an `_api` package.
- **Branching model: "assignment-flow".** A protected `main` branch receives
  PRs from `hw-N` assignment branches. **PRs are never merged**; they *are*
  the submission. Local feature work happens on `feature-<name>` branches
  off the assignment branch.

## Grading breakdown

| Component                              | Weight |
| -------------------------------------- | ------ |
| Individual updates (1% per week × 10)  | 10%    |
| Team updates (1% per week × 10)        | 10%    |
| Projects / homeworks                   | 60%    |
| Peer feedback (15% received, 5% given) | 20%    |

Roughly 10–15 extra-credit points are available per homework.

### Late policy

- 10% per day reduction.
- Partial credit beyond three days late.
- **Nothing is accepted once grading begins on the next assignment.**

### Conduct, accommodations, and inclusion

The syllabus also includes standard NYU/Tandon policies that matter for
collaborative work:

- Students requesting disability accommodations must work through the Moses
  Center for Students with Disabilities.
- Academic misconduct includes cheating, fabrication, plagiarism,
  unauthorized collaboration, duplicate work, and forged academic documents.
- Excused absences go through the Office of Student Advocacy; medical
  documentation should not be shared directly with course staff.
- The class is expected to be inclusive and respectful across backgrounds,
  identities, beliefs, abilities, and perspectives.

### Iterative submission process

Every homework follows the same rhythm:

1. **Build** a first draft. The first-draft deadline is ungraded, but
   skipping it forfeits the feedback cycle that follows.
2. **Peer + TA review.** TA feedback must be addressed; for HW1 only, peer
   feedback is optional. From HW2 onward, peer feedback must be addressed
   or have written justification for being declined.
3. **Iterate.**
4. **Final submission** is what gets graded.

## Notable rules at a glance

- The **Email** vertical is reserved for the TA reference repo
  (`https://github.com/nyu/oss-taapp-example`); teams cannot pick it.
- PRs from `hw-N` to `main` are submission artifacts and are **never merged**.
- HW2's HTTP client must be auto-generated from `/openapi.json` using
  `openapi-python-client`. Hand-rolled `requests`/`httpx` calls are
  forbidden in the adapter layer.
- HW3's vertical-API memo is graded at the **vertical** level (a shared
  grade across all teams in a vertical) and **must not be drafted with AI**.
- HW3's AI integration must support **tool calling**, not just chat.
- Documentation is treated as part of the deliverable, not an afterthought.
  HW3 explicitly says: *"Documentation is not optional."*
