# Class Reference

This section captures the course context behind this repository. It is a
condensed digest of the **OSPSD Spring 2026** syllabus, homework assignments,
and lecture decks, written so that a contributor (or grader) can understand
*why* the codebase looks the way it does without going back to the source
materials.

The course is **CS-GY 9223 / CS-UY 3943, "Special Topics in CS — Open Source
and Professional Software Development"** at NYU Tandon, taught by Profs.
Nikolai Avteniev and Kamen Yotov, Spring 2026.

## What is here

- {doc}`syllabus` — course standards, mandated stack, grading, and policies.
- {doc}`homeworks` — what HW0–HW3 each require, their rubrics, and how this
  repository maps onto them.
- {doc}`lectures` — a per-lecture summary across the 19 decks, grouped by
  theme.
- {doc}`themes` — cross-cutting concepts that recur across lectures and
  assignments and shape this codebase.
- {doc}`source-materials` — the local source-file inventory used to build
  these pages, excluding video files.

## How to read this section

These pages are reference material, not a tutorial. If you are new to the
project, start with {doc}`../getting-started` and {doc}`../architecture-overview`
instead. Come back here when you want to understand a specific course
constraint (for example, why every assignment is a PR that is never merged,
or why interfaces and implementations are split across separate packages).

```{toctree}
:hidden:
:maxdepth: 2

syllabus
homeworks
lectures
themes
source-materials
```
