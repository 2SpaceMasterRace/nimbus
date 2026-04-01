# GitHub

Git is the version control system.

GitHub is a hosting and collaboration platform built around Git repositories.

That distinction matters because a lot of beginner confusion comes from mixing the two.

Git is what creates commits, branches, merges, rebases, and history.

GitHub is where teams usually:

- host repositories,
- push branches,
- open pull requests,
- review code,
- discuss changes,
- run CI,
- track issues.

This repository uses exactly that style of workflow. The contributing guide describes the PR flow in `CONTRIBUTING.md:357-377`, and the project rules in `AGENTS.md:218-221` require branching off `main`, focused titles, and issue references.

## GitHub account basics

At minimum, for team work you need:

- a GitHub account,
- access to the repository,
- authentication set up on your machine.

If you are using SSH cloning, you also need an SSH key attached to your GitHub account.

Clone with SSH:

```console
$ git clone git@github.com:ospsd-team-2/ospsd-team-2.git
```

Typical output:

```text
Cloning into 'ospsd-team-2'...
remote: Enumerating objects: ...
Receiving objects: ...
Resolving deltas: ...
```

If you prefer HTTPS, that is also valid, but SSH is often more comfortable for repeat Git usage.

## The shape of a normal GitHub team workflow

This is the standard cycle.

1. start from `main`
2. pull the latest changes
3. create a feature branch
4. make changes and commit them
5. push the branch
6. open a pull request
7. get review comments
8. update the branch
9. let the reviewer merge after approval

This repository's contributing guide is explicit that you should not merge your own PR in the normal flow (`CONTRIBUTING.md:359-367`).

## Opening a pull request

The contributing guide says to:

1. push your branch,
2. open a PR,
3. follow the PR template,
4. keep the PR focused,
5. respond to review comments,
6. let a reviewer merge it.

Those instructions are in `CONTRIBUTING.md:357-377`.

### Browser flow

The simplest beginner flow is:

1. push your branch
2. open GitHub in the browser
3. click the prompt to compare and open a pull request
4. choose base branch and compare branch
5. fill in title and body
6. submit the PR

### CLI flow with `gh`

If you already use the terminal heavily, this is a clean flow:

```console
$ git push -u origin docs/git-chapter
$ gh pr create
$ gh pr view 10
$ gh pr checks 10
$ gh run watch
```

Typical output shape:

```text
branch 'docs/git-chapter' set up to track 'origin/docs/git-chapter'

Creating pull request for docs/git-chapter into main in ospsd-team-2/ospsd-team-2

Showing pull request #10

All checks were successful
```

The CLI details live in the terminal chapter's GitHub CLI section, but this is the collaboration flow.

## What a good pull request looks like

A good PR is:

- small enough to review,
- clearly titled,
- clear about what changed and why,
- backed by tests where appropriate,
- up to date with the base branch,
- not full of unrelated commits.

This repository's contributing guide says exactly that: keep PRs focused, follow the template, and split large changes into smaller PRs when possible (`CONTRIBUTING.md:361-377`).

Google's engineering practices make the same point very strongly: small changes are reviewed faster, reviewed more thoroughly, less likely to introduce bugs, easier to merge, easier to roll back, and less blocking on reviews. One of the most useful habits you can learn is to think of a pull request as **one self-contained change**, not as "all the work I did this week."

## Small PRs and changesets

If you are used to GitHub, the normal mental model is that a pull request is one mutable branch-shaped thing. You open the PR, reviewers comment on it, you push more commits, the PR changes under everyone's feet, and eventually it gets merged.

Mitchell Hashimoto's changesets post points out the core problem in that model: one giant mutable PR is messy. Reviews go stale, comments become partially outdated, work-in-progress commits appear before they are ready, and it becomes hard to understand what changed between one review round and the next.

The changeset idea is simple:

- a review should be attached to a specific version of a change,
- old review states should remain visible,
- and new pushes should feel like new reviewable versions, not like the old PR was silently mutated.

GitHub pull requests do not natively behave like full immutable changesets, but you can still learn from the model.

Practical beginner takeaway:

- keep PRs small,
- make each update coherent,
- avoid pushing lots of half-finished feedback commits,
- and make it easy for reviewers to know what changed since last review.

## How to break down a large feature

Mitchell Hashimoto's large-project essay has a very good habit: always break work into chunks that produce tangible progress.

That idea applies directly to GitHub workflows.

Suppose the feature is "support remote adapter downloads with stronger validation and docs updates." That is too large for one healthy PR.

A better sequence might be:

1. add a small validation helper
2. update one route to use it
3. add tests for the route change
4. update the adapter behavior
5. update docs

Or for a full-stack feature:

1. schema or data model change
2. backend behavior
3. API route or contract update
4. frontend/UI change
5. documentation and cleanup

This is also very close to the Google engineering practice of splitting CLs by files, by horizontal layers, by vertical features, and by explicit refactoring-only steps.

If you can split work so that each PR has a clear thesis, review gets easier and so does rollback.

## Team scenario: maintainer asks you to split your PR

This is one of the most common real-world review comments on a large change.

You open a 1200-line PR.

The maintainer says: this is too large, please split it.

That is not a rejection of you. It usually means the reviewer is protecting code quality and review throughput.

How to respond:

1. identify the independent parts of the work
2. separate refactoring from behavior change if possible
3. separate setup/plumbing from user-facing changes
4. create a sequence of smaller PRs
5. explicitly link them in the descriptions

This is much easier if you think in small changes early instead of after the giant PR is already written.

## Open source contribution workflow

Mitchell Hashimoto's "Contributing to Complex Projects" gives a very good progression for entering a complex codebase, and it works just as well for company repositories.

### Step 1: become a user

Before trying to change a project, use it.

For this repository, that means doing real things such as:

```console
$ uv sync --all-packages
$ uv run pytest -q
$ uv run uvicorn aws_client_service.main:app --reload
$ uv run sphinx-build docs/source docs/build/html
```

Typical output:

```text
Resolved ... packages in ...
112 passed, 1 skipped
Uvicorn running on http://127.0.0.1:8000
build succeeded.
```

The goal is not to become an expert instantly. The goal is to stop being a stranger to the system.

### Step 2: build the project

Learn how to get from source code to a working local result before you start trying to understand internals.

That is especially important in open source, where you usually cannot just ask a coworker sitting nearby.

### Step 3: learn the hot-path internals

Pick one feature you care about and trace it through the code.

For this repository, that could be:

- upload path through the service
- OAuth login flow
- adapter flow from generated client back to `CloudStorageClient`

Do not try to read the entire repository line by line first.

### Step 4: read recent commits

Look at recent commits touching the files or subsystems you care about.

That teaches you:

- what kinds of changes are accepted,
- how maintainers structure commits,
- what the recent design pressures have been.

### Step 5: make a bite-sized change

Start small.

The goal of your first contribution is not to prove genius. The goal is to learn the contribution and review process without drowning.

That is true in open source and inside companies.

## Team scenario: your first contribution to a complex project

Imagine you join a new team or want to contribute to an open source repository.

A good path is:

1. run the project locally
2. run tests locally
3. use one real feature
4. trace one hot path through the code
5. read recent commits touching that area
6. make one bite-sized change
7. follow the contribution process exactly

That path is much more reliable than starting with a huge feature request or ambitious refactor.

## Open source fork workflow in more detail

Open source often means you do not have direct push access.

The standard fork workflow is:

1. fork the repo on GitHub
2. clone your fork
3. add the original repo as `upstream`
4. branch from `upstream/main`
5. push the branch to your fork
6. open a PR from your fork to the original repo

Setup:

```console
$ git clone git@github.com:yourname/ospsd-team-2.git
$ cd ospsd-team-2
$ git remote add upstream git@github.com:ospsd-team-2/ospsd-team-2.git
$ git fetch upstream
$ git switch -c fix-doc-typo upstream/main
```

Typical output:

```text
Cloning into 'ospsd-team-2'...
From github.com:ospsd-team-2/ospsd-team-2
 * [new branch]      main       -> upstream/main
Switched to a new branch 'fix-doc-typo'
```

Then push to your own fork:

```console
$ git push -u origin fix-doc-typo
```

Typical output:

```text
Enumerating objects: ...
Writing objects: ...
remote: Create a pull request for 'fix-doc-typo' on GitHub by visiting:
remote:   https://github.com/yourname/ospsd-team-2/pull/new/fix-doc-typo
branch 'fix-doc-typo' set up to track 'origin/fix-doc-typo'.
```

Then open the PR against the main repository.

## Team scenario: your open source PR gets no response for a while

This happens.

Open source maintainers are often overloaded.

Good responses:

- check whether the repo has stated response expectations
- make sure CI passes
- make sure the PR is clearly scoped
- leave a polite follow-up after a reasonable wait
- avoid noisy repeated pings

If the change is still ignored, you may need to reduce scope further or move to a smaller starter issue.

## Stacked diffs and stacked PRs

Stacked diffs are a way to split one larger effort into a sequence of smaller dependent review units.

Instead of one huge PR, you might have:

- PR 1: internal plumbing
- PR 2: API change built on PR 1
- PR 3: UI change built on PR 2
- PR 4: cleanup or follow-up docs

This is useful because:

- review units are smaller
- authors stay unblocked
- reviewers can focus on one layer at a time
- merge order becomes more explicit

Mitchell's changesets post, the stacking workflow site, Ben Congdon's essay, Jackson Gabbard's post, and the Pragmatic Engineer discussion all point at the same basic truth: small, dependent changes are often easier to review than one giant branch-shaped PR.

## When stacked diffs help most

Stacked workflows shine when:

- a large feature breaks naturally into dependency layers
- different reviewers own different layers
- you want to keep coding while earlier parts are in review
- each piece is safe and meaningful on its own

Typical examples:

- schema -> backend -> API -> UI
- compatibility shim -> migrated callers -> cleanup
- refactor -> behavior change -> docs

## Team scenario: one feature naturally wants four PRs

Suppose you need to add a new service endpoint and a frontend consumer for it.

A healthy stack could be:

1. add internal abstraction or helper
2. add backend endpoint
3. add generated client update
4. add frontend or caller usage

This keeps review units smaller and lets backend work land earlier if it is independently safe.

## Risks and tradeoffs of stacked diffs

Stacked diffs are not free.

They introduce:

- more rebasing
- more dependency bookkeeping
- more branch or commit-stack management
- more chances to confuse reviewers if the stack is undocumented

If the decomposition is poor, stacked PRs can become fake-small rather than truly understandable.

Use stacking when the code is naturally layered, not just because stacking sounds advanced.

## GitHub versus true changesets

GitHub pull requests are still branch-oriented and mutable.

That means a reviewer can be looking at one version while the branch changes underneath them.

This is part of why changeset-oriented tools exist. They try to give better structure to multi-step review.

Without extra tooling, you can still approximate stacked review on GitHub by:

- creating dependent branches,
- opening dependent PRs,
- linking them clearly,
- keeping each PR tiny,
- updating them carefully when lower layers change.

## Tools for stacked workflows

You do not need special tools to learn the concept, but tooling can remove a lot of branch and rebase pain.

Useful tools in this space include:

- **Graphite** for stack-aware GitHub workflows
- **ghstack** for stacked diffs on GitHub
- **spr** for submit-and-update PRs from amendable/rebaseable commits
- **git-grok** for commit-centric stacked PR workflows on GitHub
- **git-branchless** for branchless or patch-stack-friendly local workflows
- **git-machete** for stacked-branch organization
- **git-stack** for stacked branch management
- **Git Town** for higher-level branch workflow automation
- **Sapling** for a source control system with built-in support for stacked work
- **ReviewStack** as another stack-aware review tool

These tools are not all the same, but they share a goal: reduce the manual bookkeeping around dependent changes.

## Team scenario: reviewer asks for changes in the bottom PR of your stack

This is one of the hardest real stacked-workflow moments.

You have PR A, PR B, and PR C.

Reviewer requests changes in PR A.

Now B and C may need to be rebased or restacked on top of the new A.

With plain Git, this can mean careful rebasing and possibly force-pushing multiple branches.

Helpful tools here include:

- `git rebase --update-refs` in newer Git
- stack-aware tools like Graphite, spr, git-grok, or git-branchless

This is exactly why stack tooling exists.

## Team scenario: you want to insert a new PR in the middle of an existing stack

This is another classic stacked-diff problem.

You realize there should be a missing refactor layer between PR A and PR B.

Now you need to:

1. create the missing change
2. rebase later work on top of it
3. update the dependent PRs

This is possible with plain Git, but tooling makes it much less painful.

## Small review units as a team habit

Google's engineering practices make a very strong argument for small change lists:

- faster review
- more thorough review
- less risk
- easier rollback
- easier design quality
- less blocking
- simpler merges

That applies whether you use plain PRs or stacked diffs.

The real lesson is not "always stack." The real lesson is "make the unit of review as small and self-contained as you reasonably can."

## Team scenario: large project, many reviewers, different ownership areas

Suppose a feature touches:

- backend data model
- service layer
- generated client
- docs

If you put all of that in one giant PR, reviewers may struggle to know which parts they own.

A better approach is often:

- one small PR per ownership boundary
- explicit dependencies between them
- explicit reviewer expectations in the PR descriptions

This is one of the strongest reasons to use layered or stacked review.

## Building large technical projects without waiting for giant merges

Mitchell Hashimoto's large-project essay makes a very useful point: break large work into chunks that produce visible progress and real demos quickly.

For GitHub workflows, that translates well into:

- a working branch structure
- a sequence of meaningful review units
- a way to keep moving even while earlier changes are under review

That is another strong argument for smaller PRs and, when needed, stacked ones.

## Team scenario: opening your first PR on a branch

```console
$ git switch main
$ git pull origin main
$ git switch -c fix-upload-error-handling
```

Work.

Commit.

Run checks:

```console
$ uv run ruff check .
$ uv run ruff format --check .
$ uv run mypy --strict .
$ uv run pytest
```

Push:

```console
$ git push -u origin fix-upload-error-handling
```

Open the PR.

That is the bread-and-butter team workflow.

## Team scenario: review comments arrived

The reviewer asks for:

- a rename,
- one extra test,
- a docs update.

Make those changes on the same branch.

Then:

```console
$ git add ...
$ git commit -m "address review feedback on upload error handling"
$ git push
```

The PR updates automatically.

Then respond to the comments.

This repository explicitly says not to push changes silently without responding to review comments (`CONTRIBUTING.md:365-366`).

## Team scenario: your PR is stale

You opened the PR two days ago. `main` moved. GitHub says the branch is out of date.

Bring the branch up to date locally.

Merge approach:

```console
$ git switch fix-upload-error-handling
$ git fetch origin
$ git merge origin/main
```

Rebase approach:

```console
$ git switch fix-upload-error-handling
$ git fetch origin
$ git rebase origin/main
```

Typical output:

```text
From github.com:...
Successfully rebased and updated refs/heads/fix-upload-error-handling.
```

Then push the updated branch.

If you rebased, you may need:

```console
$ git push --force-with-lease
```

Typical output:

```text
Enumerating objects: ...
Writing objects: ...
+ refs/heads/fix-upload-error-handling:refs/heads/fix-upload-error-handling (forced update)
```

Use `--force-with-lease`, not plain `--force`, because it is safer.

## Team scenario: the PR shows merge conflicts

This means your branch and the base branch now disagree in the same place.

Resolve it locally.

Typical flow:

```console
$ git switch fix-upload-error-handling
$ git fetch origin
$ git merge origin/main
```

Typical output when there is a conflict:

```text
Auto-merging docs/guide/02_git/02_github.md
CONFLICT (content): Merge conflict in docs/guide/02_git/02_github.md
Automatic merge failed; fix conflicts and then commit the result.
```

Or with rebase:

```console
$ git switch fix-upload-error-handling
$ git fetch origin
$ git rebase origin/main
```

Typical output when there is a conflict:

```text
Auto-merging docs/guide/02_git/02_github.md
CONFLICT (content): Merge conflict in docs/guide/02_git/02_github.md
error: could not apply abc1234... add github guide chapter
```

Then:

1. fix the conflicted files manually
2. `git add` the resolved files
3. finish the merge or rebase
4. push the updated branch

## Team scenario: you rebased and now need to push

After a rebase, your commit IDs changed.

That means your local branch history no longer matches the remote branch history.

Push with:

```console
$ git push --force-with-lease
```

Why `--force-with-lease`?

Because it refuses to overwrite remote history if the remote moved in a way you did not expect.

That is much safer for team work than plain `--force`.

## Team scenario: someone force-pushed the branch you were reviewing

This is a real team problem.

You were reading a PR. Suddenly the diff changed shape because the author rebased and force-pushed.

What to do:

1. refresh your local view
2. inspect the new history shape
3. do not assume the old comments still apply line-for-line
4. reread the changed areas

Useful commands:

```console
$ git fetch origin
$ git log --oneline --graph --decorate --all
$ git reflog
```

Typical output shape:

```text
* abc1234 (origin/fix-upload-error-handling) rewritten branch tip
| * 98de765 (HEAD -> fix-upload-error-handling) your previous local tip
|/
...
```

This is one reason teams often prefer fewer, more deliberate force-pushes.

## Team scenario: your teammate asked you to review another branch while your work is mid-flight

Use a worktree.

```console
$ git worktree add ../review-branch teammate-branch
$ cd ../review-branch
```

Typical output:

```text
Preparing worktree (checking out 'teammate-branch')
HEAD is now at abc1234 teammate branch commit
```

Now you can run tests and inspect that branch without disturbing your own working tree.

## Team scenario: a bad commit already landed on `main`

On a shared branch, use `revert`, not `reset`.

```console
$ git switch main
$ git pull origin main
$ git revert <sha>
$ git push origin main
```

Typical output:

```text
[main fedcba9] Revert "bad change"
 1 file changed, 10 deletions(-)
To github.com:ospsd-team-2/ospsd-team-2.git
   abc1234..fedcba9  main -> main
```

This creates a new commit that undoes the old one while preserving shared history.

## Team scenario: you accidentally pushed a secret

Treat this as an incident, not just a Git cleanup.

Immediate steps:

1. rotate the secret in the real service
2. stop tracking the file if needed
3. coordinate with the team about whether history rewriting is required

Example untracking command:

```console
$ git rm --cached .env
```

Typical output:

```text
rm '.env'
```

This repository explicitly forbids committing secrets (`AGENTS.md:232-236`).

## Branch protection and CI

In many teams, GitHub is not just a code host. It is also the gatekeeper for merging.

Common protections include:

- required status checks
- required reviews
- blocked direct pushes to protected branches
- blocked force-pushes to important branches

This repository's contributing guide also expects checks to pass before pushing and before PR merge (`CONTRIBUTING.md:344-377`).

The CircleCI workflow in this repo shows the basic CI path:

- lint
- unit tests
- integration tests
- e2e tests
- deploy on the `hw-2` branch flow (`.circleci/config.yml:142-162`)

## Opening a PR in an open source fork workflow

Not every team gives everyone push access to the main repository.

Sometimes the flow is:

1. fork the repo on GitHub
2. clone your fork
3. add the original repo as `upstream`
4. push branches to your fork
5. open a PR from your fork to the original repo

Typical setup:

```console
$ git clone git@github.com:yourname/ospsd-team-2.git
$ cd ospsd-team-2
$ git remote add upstream git@github.com:ospsd-team-2/ospsd-team-2.git
$ git fetch upstream
```

Then branch from `upstream/main` and push to `origin`.

## A practical PR checklist

Before opening or updating a PR in this repository, the expected local checklist is:

```console
$ uv run ruff check .
$ uv run ruff format --check .
$ uv run mypy --strict .
$ uv run pytest
```

Typical output shape:

```text
All checks passed!
Success: no issues found in ... source files
112 passed, 1 skipped
```

That comes directly from `CONTRIBUTING.md:344-353`.

## GitHub workflow in this repository specifically

From `CONTRIBUTING.md:357-377` and `AGENTS.md:218-221`:

- branch from `main`
- do not push directly to protected shared flow
- keep PRs focused
- follow the PR template
- respond to review comments
- let a reviewer merge
- use titles focused on user-visible impact
- reference issues when appropriate

That is the working social contract around Git in this project.

## Further reading

- Pro Git GitHub section: <https://git-scm.com/book/en/v2/GitHub-Account-Setup-and-Configuration>
- Pro Git distributed workflows: <https://git-scm.com/book/en/v2/Distributed-Git-Distributed-Workflows>
- Google engineering practices on small CLs: <https://google.github.io/eng-practices/review/developer/small-cls.html>
- Google review guidance: <https://google.github.io/eng-practices/review/reviewer/looking-for.html>
- Mitchell Hashimoto, changesets: <https://mitchellh.com/writing/github-changesets>
- Mitchell Hashimoto, large technical projects: <https://mitchellh.com/writing/building-large-technical-projects>
- Mitchell Hashimoto, contributing to complex projects: <https://mitchellh.com/writing/contributing-to-complex-projects>
- The stacking workflow: <https://www.stacking.dev/>
- Stacked diffs overview: <https://jg.gg/2018/09/29/stacked-diffs-versus-pull-requests/>
- In praise of stacked PRs: <https://benjamincongdon.me/blog/2022/07/17/In-Praise-of-Stacked-PRs/>
- git-grok: <https://github.com/dimikot/git-grok>
- git-branchless: <https://github.com/arxanas/git-branchless>
- spr: <https://github.com/spacedentist/spr>
- Git Town: <https://git-town.com/>
- `CONTRIBUTING.md:357-377`
- `AGENTS.md:218-236`
