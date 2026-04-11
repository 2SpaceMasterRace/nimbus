# AI Coding Tools and Agents

This chapter is about how to use AI coding tools as part of a real terminal workflow.

It follows a simple progression.

## Step 1: Drop the Chatbot

Stop trying to do meaningful repository work inside a plain chat window.

A chatbot is fine for:

- asking what an error means,
- summarizing an article,
- exploring a concept,
- brainstorming names,
- generating a rough sketch.

A chatbot is weak for real repository work because it usually does not have enough tools, enough context, or enough feedback.

For coding work, use an agent.

At a minimum, the agent should be able to:

- read files,
- run commands,
- inspect output,
- make HTTP requests if needed,
- loop until it gets more evidence.

That is the practical difference between "talking about code" and "working on code."

## Step 2: Reproduce Your Own Work

One of the fastest ways to learn where agents help is to reproduce work you already know how to do.

That means:

1. do the task manually,
2. then ask the agent to do the same task independently,
3. compare the result,
4. tighten your prompts until the quality becomes acceptable.

This is slow at first, but it teaches very quickly.

It teaches:

- what tasks the agent is good at,
- what tasks it is bad at,
- how much structure your prompts need,
- what kinds of checks help it recover,
- where your harness is weak.

If you skip this stage, agents often keep feeling random forever.

## Step 3: Break Work into Separate Sessions

Do not try to "draw the owl" in one mega prompt.

Break work into separate clear sessions.

Good sessions:

- audit one code path,
- write one docs section,
- fix one failing test,
- build one subfeature,
- refactor one file,
- review one diff,
- regenerate one artifact.

Bad sessions:

- rewrite the whole app,
- fix everything,
- make it better,
- implement the full feature plus tests plus docs plus deployment in one shot.

For vague work, split planning and execution.

That means one session makes a plan and another session performs the work.

## Step 4: Prototype First

If you do not fully know what you want yet, prototype.

This is especially good for:

- UI direction,
- docs structure,
- test shape,
- naming alternatives,
- design sketches,
- exploratory refactors.

Do not expect the first prototype to be the final result.

Often the prototype's job is to help you discover what you want.

## Step 5: Use the Agent as a Muse

Sometimes the best value from a session is not the code you keep.

Sometimes the value is that the agent gives you:

- a structure you like,
- a naming pattern you like,
- a workflow idea,
- a UI direction,
- or a rough draft that helps you see the next move.

Throwing away output is normal.

You can benefit from a session even if you keep none of the generated code.

## Step 6: Recognize the Slop Zone

There is a point where an agent stops making real progress and starts producing expensive noise.

That is the slop zone.

Common signs:

- repeated failed fixes,
- changing the wrong files,
- explanations that no longer match the code,
- too much code and not enough insight,
- each iteration making the diff worse,
- your own understanding getting weaker.

When that happens, stop.

Do not keep prompting just because you already spent time on the session.

Change strategy.

Inspect the code yourself. Reduce the problem. Narrow the task. Or stop using the agent for that specific step.

## Step 7: Run Cleanup Sessions

Cleanup sessions matter.

After an agent writes code, run follow-up sessions for:

- moving code to better places,
- renaming things,
- deleting dead code,
- reducing duplication,
- tightening docs and comments,
- making the structure clearer for the next session.

Future agent quality depends heavily on current code quality.

Messy code creates messy follow-up sessions.

Clean code produces better future work.

## Step 8: Fill in the Blanks

Agents are often strongest when the structure already exists.

A good pattern is:

1. create the file or function skeleton yourself,
2. add names, parameters, TODO comments, and rough structure,
3. then ask the agent to complete the missing work.

Example:

```text
Read AGENTS.md. Complete the TODOs in src/aws_client_adapter/aws_client_adapter/service_adapter.py. Preserve the current structure. Run uv run pytest -q afterwards.
```

This is usually better than asking the agent to invent the architecture from scratch.

## Step 9: Use Simulations and Tests Aggressively

Agents are often good at generating:

- test scaffolding,
- simulation cases,
- repetitive validation coverage,
- known setup patterns,
- output variations for edge cases.

This is especially useful when you already know the scenarios you want, but do not want to hand-write the repetitive glue.

In this repository, useful agent-generated outputs often include:

- route tests,
- adapter error-translation tests,
- docs build checks,
- OpenAPI regeneration support,
- review reports on a PR or branch.

## Step 10: Use End-of-Day Agents

When your own workday is winding down, start agents on useful background tasks.

Good end-of-day tasks:

- deep research,
- repository surveys,
- issue triage,
- PR review reports,
- plan drafts,
- low-risk cleanup,
- prototypes you will inspect tomorrow.

The point is not to keep agents running for sport.

The point is to turn dead time into useful progress.

## Step 11: Outsource the Slam Dunks

Once you know what the agent does reliably, give it the easy wins while you work on something harder.

Typical slam-dunk tasks:

- repetitive docs rewrites,
- mechanical refactors,
- issue triage,
- test expansions with an obvious pattern,
- config cleanups,
- generated artifact updates,
- error-shape translation once the target pattern is clear.

This is one of the first places where AI starts feeling like a real multiplier.

## Step 12: Turn Off Notifications

Do not let the agent control your attention.

If the tool supports desktop notifications, disable them or use them sparingly.

Context switching is expensive.

The human should decide when to check on the agent, not the other way around.

Good pattern:

- let the agent work,
- check in during natural pauses,
- intervene only when necessary,
- then return to your own task.

## Step 13: Engineer the Harness

This is the long-term play.

When the agent repeats a mistake, improve the environment so it stops making that mistake.

There are two main ways to do that.

### Better instructions

This repository already has a strong harness file: `AGENTS.md`.

It contains:

- project overview,
- repo structure,
- install commands,
- test commands,
- architecture constraints,
- testing philosophy,
- environment variable expectations,
- Git and PR rules,
- safety boundaries.

That is exactly the sort of file that makes both humans and agents more effective.

### Better tools

If the same verification or inspection step keeps mattering, turn it into a script, wrapper, or dedicated command.

Examples:

- screenshot helpers,
- filtered test commands,
- build wrappers,
- docs render commands,
- project-specific verification scripts.

The rule is simple: when the same mistake repeats, fix the environment, not just the next prompt.

## Step 14: Always Have an Agent Running When It Makes Sense

Do not run agents just to say you are running agents.

Do run them when there is genuinely useful work to delegate.

The practical question is:

> is there something useful an agent could be doing for me right now while I do something else?

If the answer is yes, keep one running.

Not ten. Not chaos. Just a useful background worker.

## OpenCode and AmpCode in this model

OpenCode and AmpCode fit well into this workflow when used as tool-using agents rather than glorified chat windows.

Good uses:

- repository exploration,
- implementing well-scoped tasks,
- repetitive edits,
- draft tests,
- PR and issue reports,
- overnight or end-of-day research,
- prototypes you will refine manually.

Bad uses:

- vague one-shot rewrites,
- shipping code without review,
- skipping repository rules,
- skipping verification,
- letting the tool drive your context switching.

## Use `AGENTS.md`

Always point the agent at `AGENTS.md` for this repository.

That file tells the agent how to:

- understand the repository,
- respect boundaries,
- run the right commands,
- avoid destructive behavior,
- follow the project's architecture,
- and work in the repo's expected style.

Useful companion files to point the agent at:

- `AGENTS.md`
- `README.md`
- `DESIGN.md`
- `pyproject.toml`

## Good prompt patterns

### Audit

```text
Read AGENTS.md. Audit src/aws_client_impl/aws_client_impl/s3_client.py for validation and error-translation problems. Report concrete findings with file and line references.
```

### Implementation

```text
Read AGENTS.md. Update src/aws_client_service/aws_client_service/main.py so the route validates query parameters consistently with the existing routes. Add or update tests. Run uv run pytest -q.
```

### Documentation

```text
Read AGENTS.md. Write docs/guide/05_testing.md in the existing guide style. Use real file references from this repo. Build the docs with uv run sphinx-build docs/source docs/build/html.
```

### Review

```text
Read AGENTS.md. Review the current branch for correctness issues and missing tests. Focus on behavior regressions, not style.
```

## Bad prompt patterns

```text
Make the project better.
Rewrite everything.
Fix all bugs.
Build the entire feature in one shot.
```

These are hard to review and hard for the agent to self-correct.

## Verification for this repository

These are the main verification commands for this repo:

```console
$ uv run pytest -q
$ uv run ruff check .
$ uv run mypy --strict .
$ uv run sphinx-build docs/source docs/build/html
```

Docs-only work:

```console
$ uv run sphinx-build docs/source docs/build/html
```

Code-focused work:

```console
$ uv run ruff format .
$ uv run ruff check .
$ uv run mypy --strict .
$ uv run pytest -q
```

If the agent can run checks, it can often fix many of its own regressions.

## Terminal layout for agent work

Use a dedicated `tmux` pane or window for the agent.

Good layout:

- editor in one pane,
- verification commands in another,
- agent in a third.

Main shell pane:

```console
$ uv run pytest -q
$ uv run ruff check .
$ uv run mypy --strict .
$ uv run sphinx-autobuild docs/source docs/build/html
```

Agent pane:

- read `AGENTS.md`
- inspect files
- make edits
- run checks
- report results

## The final manual review

This is the rule that stays at the end no matter how good the tools get.

Do not ship important AI-written code without a real manual review.

Review:

- correctness,
- architecture,
- naming,
- duplication,
- tests,
- diffs,
- edge cases,
- unintended side effects.

If you do not understand a piece of code, do not ship it just because the agent sounds confident.

## Today

The most useful way to think about AI coding tools is not as replacements for engineering judgment, but as assistants that become more valuable as your workflow gets sharper.

The better you are at:

- scoping work,
- reviewing diffs,
- designing harnesses,
- building verification loops,
- and knowing when not to delegate,

the better the tools tend to work.

In that sense, agent workflows reward engineering maturity rather than replacing it.

## Further reading

- `AGENTS.md`
- `README.md`
- `DESIGN.md`
