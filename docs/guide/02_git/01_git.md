# Git

Git is a version control system. That means it helps you track changes to a project over time.

The official Pro Git book explains Git's most important idea like this: Git stores snapshots, not just line-by-line differences. Every commit is a snapshot of the project at a particular moment.

That sentence is the key.

Git is not mostly thinking, "what changed in this one file?" Git is thinking, "what did the project look like at this point in history?"

## What Git is in simple words

Here is the ELI5 version.

- your project is a folder
- Git remembers important pictures of that folder
- each picture is a **commit**
- a **branch** is a named path through those commits
- your machine has the full history locally

The Frontend Masters Git course also emphasizes that Git is **distributed**. That means your local machine has the repository history and can do most operations without talking to a server.

## Git thinks in snapshots

Most beginners imagine version control as a list of file edits.

Git does not primarily think that way.

Git stores the project as a stream of snapshots. If a file did not change, Git usually stores a reference to the already-known version instead of storing a whole duplicate again.

That is why commits are such a powerful unit of thought. A commit is not just "some lines changed." It is "this is what the project looked like at this moment."

## Git is fast because most operations are local

The Pro Git book also stresses that nearly every operation is local.

That means commands like:

```console
$ git log
$ git diff
$ git show HEAD
$ git blame file.py
```

are usually reading your local repository data, not waiting on the network.

## Git has integrity built in

Git identifies objects using hashes. That is why you see commit IDs such as:

```text
24b9da6552252987aa493b52f8696cd6d3b00373
```

That hash-based identity is part of why Git can detect corruption and why commit references are so powerful.

## Git mostly adds data

Another very useful Pro Git idea is that Git generally adds data rather than mutating history in place.

That is why so many mistakes are recoverable if you know where to look.

That recovery story becomes much easier once you understand the three main places Git cares about.

## The three places Git cares about

If you only learn one Git model, learn this one.

Files move through three major places:

- the **working tree**
- the **staging area**
- the **repository history**

The corresponding file states are:

- **modified**
- **staged**
- **committed**

### Working tree

The working tree is what is in your filesystem right now.

If you edit `README.md`, you are changing the working tree.

### Staging area

The staging area is Git's "this is what I want in the next commit" shelf.

You choose what goes there with `git add`.

### Repository history

This is where committed snapshots live.

If you remember this model, commands like `git add`, `git diff`, `git restore`, and `git commit` become much easier to reason about.

## First-time setup

Check Git:

```console
$ git --version
```

Typical output:

```text
git version 2.47.0
```

Set your name and email:

```console
$ git config --global user.name "Your Name"
$ git config --global user.email "you@example.com"
```

Set your default branch name if you want:

```console
$ git config --global init.defaultBranch main
```

Set your editor:

```console
$ git config --global core.editor "nvim"
```

List your config:

```console
$ git config --list
```

Typical output includes lines such as:

```text
user.name=Your Name
user.email=you@example.com
core.editor=nvim
init.defaultbranch=main
```

Show where a config value came from:

```console
$ git config --show-origin --get user.email
```

Typical output:

```text
file:/Users/you/.gitconfig  you@example.com
```

The Frontend Masters course spends time on config locations, and that is worth learning. In practice, you will care most about:

- global config for your machine
- local config for a repository

Show local config for the current repo:

```console
$ git config --local --list
```

If the repo has no local overrides yet, this may print nothing.

## Getting help

One of the best habits in the Frontend Masters course is to use the man pages.

Examples:

```console
$ git help commit
$ git help rebase
$ man git
```

If you forget a command shape, ask Git directly.

## Starting a repository

Create a new repository:

```console
$ git init
```

Typical output:

```text
Initialized empty Git repository in /path/to/project/.git/
```

Clone an existing repository:

```console
$ git clone git@github.com:ospsd-team-2/ospsd-team-2.git
```

Typical output:

```text
Cloning into 'ospsd-team-2'...
remote: Enumerating objects: ...
remote: Counting objects: ...
Receiving objects: ...
Resolving deltas: ...
```

This repository's setup docs use that exact pattern (`README.md:103-109`, `CONTRIBUTING.md:79-85`).

After cloning:

```console
$ cd ospsd-team-2
$ git status
```

Typical output:

```text
On branch main
Your branch is up to date with 'origin/main'.

nothing to commit, working tree clean
```

## The daily command set

Check status:

```console
$ git status
$ git status --short
```

Typical output for a clean repo:

```text
On branch main
nothing to commit, working tree clean
```

Typical short output for a clean repo:

```text
(no output)
```

Stage files:

```console
$ git add README.md
$ git add .
$ git add -p
```

Typical output:

```text
(usually no output)
```

For `git add -p`, Git shows hunks interactively, for example:

```text
Stage this hunk [y,n,q,a,d,e,?]?
```

Commit:

```console
$ git commit -m "update setup instructions"
```

Typical output:

```text
[main abc1234] update setup instructions
 1 file changed, 3 insertions(+)
```

Inspect history and diffs:

```console
$ git log --oneline --graph --decorate
$ git diff
$ git diff --staged
$ git show HEAD
```

Typical `git log --oneline --graph --decorate` output:

```text
* abc1234 (HEAD -> main) update setup instructions
* 98de765 previous commit message
```

## A tiny real example

You edit `README.md`.

Check status:

```console
$ git status
```

Typical output after editing a tracked file:

```text
On branch main
Changes not staged for commit:
  modified:   README.md

no changes added to commit
```

Stage it:

```console
$ git add README.md
```

Typical output:

```text
(no output)
```

Commit it:

```console
$ git commit -m "update setup instructions"
```

Typical output:

```text
[main def5678] update setup instructions
 1 file changed, 2 insertions(+), 1 deletion(-)
```

That is the core loop.

working tree -> staging area -> commit history

## Tracked and untracked files

- **tracked** means Git already knows about the file
- **untracked** means Git sees the file but is not yet including it in history

That is why a new file appears in `git status` as untracked until you add it.

## `.gitignore`

Some files should never be committed.

Typical examples:

- secrets
- local `.env` files
- generated artifacts
- caches
- virtual environments

The repo already ignores local secret and homework artifacts in `.gitignore`, and the project rules explicitly say never commit secrets or tokens (`AGENTS.md:232-236`).

Typical ignore lines:

```text
.env
__pycache__/
.venv/
*.log
```

If a file is already tracked, adding it to `.gitignore` does not magically untrack it. Use:

```console
$ git rm --cached path/to/file
```

Typical output:

```text
rm 'path/to/file'
```

## Commit messages

This repository has strong commit guidance.

From `CONTRIBUTING.md:326-343` and `AGENTS.md:213-221`:

- write commit messages in the imperative mood
- explain the *why* and high-level *what*
- keep commits focused

Good examples:

```text
fix multipart upload part numbering bug
add retry logic to upload_file
update CONTRIBUTING.md with AWS setup steps
```

Bad examples:

```text
fixed bug
updates
stuff
```

## Branches

A branch is a movable name pointing at a commit.

In simpler language: a branch is a separate line of work.

Examples:

- `main`
- `fix-upload-error-handling`
- `docs/git-chapter`

Create and switch to a branch:

```console
$ git switch -c fix-upload-error-handling
```

Typical output:

```text
Switched to a new branch 'fix-upload-error-handling'
```

List branches:

```console
$ git branch
```

Typical output:

```text
* fix-upload-error-handling
  main
```

Switch branches:

```console
$ git switch main
```

Typical output:

```text
Switched to branch 'main'
Your branch is up to date with 'origin/main'.
```

Delete a fully merged branch:

```console
$ git branch -d fix-upload-error-handling
```

Typical output:

```text
Deleted branch fix-upload-error-handling (was abc1234).
```

Force-delete a branch:

```console
$ git branch -D scratch-branch
```

The repo's documented workflow is to branch from `main` and use short descriptive names (`CONTRIBUTING.md:314-325`).

## Team scenario: start a normal feature branch

```console
$ git switch main
$ git pull origin main
$ git switch -c docs/git-chapter
```

Typical output:

```text
Already on 'main'
Already up to date.
Switched to a new branch 'docs/git-chapter'
```

Work.

Check status and diffs:

```console
$ git status
$ git diff
```

Typical output after editing docs:

```text
On branch docs/git-chapter
Changes not staged for commit:
  modified:   docs/guide/02_git/01_git.md
```

Stage and commit:

```console
$ git add docs/guide/02_git.md docs/source/guide/02_git.md docs/source/guide/index.md
$ git commit -m "add git guide chapter"
```

Typical output:

```text
[docs/git-chapter 12ab34c] add git guide chapter
 3 files changed, 120 insertions(+)
```

Push:

```console
$ git push -u origin docs/git-chapter
```

Typical output:

```text
Enumerating objects: ...
Counting objects: ...
Writing objects: ...
remote: Create a pull request for 'docs/git-chapter' on GitHub by visiting:
remote:   https://github.com/.../pull/new/docs/git-chapter
branch 'docs/git-chapter' set up to track 'origin/docs/git-chapter'.
```

## Team scenario: you started working on the wrong branch

This happens constantly.

You meant to work on `docs/git-chapter`, but you made changes on `main`.

If you have not committed yet:

```console
$ git stash push -m "work from wrong branch"
$ git switch -c docs/git-chapter
$ git stash pop
```

Typical output:

```text
Saved working directory and index state On main: work from wrong branch
Switched to a new branch 'docs/git-chapter'
On branch docs/git-chapter
Changes not staged for commit:
  modified:   README.md
Dropped refs/stash@{0}
```

If you already committed on the wrong branch and it is still local:

```console
$ git branch docs/git-chapter
$ git switch docs/git-chapter
$ git switch main
$ git reset --hard HEAD~1
```

Use that second pattern carefully. If the commit was already pushed or shared, do not casually rewrite shared history.

## Merge and rebase

When two branches both have new commits, you need to combine them somehow.

The two main tools are:

- `merge`
- `rebase`

### Merge

Merge combines histories and may create a merge commit.

```console
$ git switch main
$ git merge docs/git-chapter
```

Fast-forward example output:

```text
Updating abc1234..def5678
Fast-forward
 docs/guide/02_git/01_git.md | 120 +++++++++++++++++
```

Three-way merge example output:

```text
Merge made by the 'ort' strategy.
 docs/guide/02_git/01_git.md | 120 +++++++++++++++++
```

### Rebase

Rebase replays your branch commits on top of a newer base.

```console
$ git switch docs/git-chapter
$ git rebase main
```

Typical output:

```text
Successfully rebased and updated refs/heads/docs/git-chapter.
```

Simple practical rule:

- rebasing your own local feature branch is normal
- rebasing a branch others are already using requires care
- never casually rewrite shared public history

## Team scenario: your branch is behind `main`

You worked for two days. `main` moved.

### Option 1: merge `main` into your branch

```console
$ git switch docs/git-chapter
$ git fetch origin
$ git merge origin/main
```

Typical output:

```text
Already on 'docs/git-chapter'
From github.com:...
 * branch            main       -> FETCH_HEAD
Merge made by the 'ort' strategy.
```

### Option 2: rebase your branch onto `main`

```console
$ git switch docs/git-chapter
$ git fetch origin
$ git rebase origin/main
```

Typical output:

```text
From github.com:...
Successfully rebased and updated refs/heads/docs/git-chapter.
```

If the team prefers a clean branch history, rebase your own branch before updating the PR.

## Merge conflicts

A merge conflict means Git found edits in the same area and cannot decide what you intended.

Conflict markers look like this:

```text
<<<<<<< HEAD
your current branch version
=======
incoming version
>>>>>>> other-branch
```

After fixing the file:

```console
$ git add path/to/conflicted-file
```

Typical output:

```text
(no output)
```

If you are finishing a merge:

```console
$ git commit
```

If you are finishing a rebase:

```console
$ git rebase --continue
```

Abort a rebase:

```console
$ git rebase --abort
```

Abort a merge:

```console
$ git merge --abort
```

## Team scenario: you and a teammate changed the same file

You changed `README.md` to rename a command.

Your teammate changed the same lines to add another explanation.

You pull or rebase and Git reports a conflict.

Do this:

1. run `git status`
2. open the conflicted file
3. read both versions carefully
4. write the final combined result manually
5. remove the conflict markers
6. `git add` the resolved file
7. finish the merge or rebase

Do not blindly choose one side unless that really is what you want.

## Team scenario: you keep getting the same conflict again and again

Use `rerere`.

Enable it:

```console
$ git config --global rerere.enabled true
```

`rerere` means "reuse recorded resolution." Git remembers how you resolved a conflict before and can often replay that resolution next time.

This is especially helpful on long-lived branches that repeatedly conflict with `main`.

## Ours and theirs

Git has the ideas of "ours" and "theirs," but beginners should be careful because the words feel slippery across merge and rebase.

Safer rule:

- inspect the actual content
- choose the final result deliberately

Later you can use ours/theirs strategies more confidently.

## Stash

Stash is a temporary shelf for uncommitted work.

Create a stash:

```console
$ git stash
```

Typical output:

```text
Saved working directory and index state WIP on docs/git-chapter: abc1234 add git guide chapter
```

List stashes:

```console
$ git stash list
```

Typical output:

```text
stash@{0}: On docs/git-chapter: half-done docs edit
```

Show one stash:

```console
$ git stash show -p stash@{0}
```

Apply a stash without removing it:

```console
$ git stash apply stash@{0}
```

Pop a stash and remove it:

```console
$ git stash pop
```

Name a stash:

```console
$ git stash push -m "half-done docs edit"
```

## Worktrees

Worktrees let one Git repo have multiple working directories checked out at once.

Add a worktree for another branch:

```console
$ git worktree add ../ospsd-team-2-pr-review main
```

Create a new branch in a new worktree:

```console
$ git worktree add -b docs/git-chapter ../ospsd-team-2-git-docs main
```

List worktrees:

```console
$ git worktree list
```

Remove a worktree:

```console
$ git worktree remove ../ospsd-team-2-pr-review
```

### Team scenario: review a PR without disturbing your branch

Instead of stashing and switching back and forth:

```console
$ git worktree add ../review-branch other-person-branch
$ cd ../review-branch
```

Now you can inspect, run tests, or review that branch in a separate directory while your main worktree stays untouched.

## HEAD and reflog

`HEAD` means where you currently are.

The reflog is Git's local journal of where HEAD and branch references have been.

Show reflog:

```console
$ git reflog
```

Typical output:

```text
abc1234 HEAD@{0}: commit: add git guide chapter
98de765 HEAD@{1}: checkout: moving from main to docs/git-chapter
...
```

### Team scenario: you lost your branch tip

Maybe you reset too far, deleted a branch, or rebased badly.

Start with:

```console
$ git reflog
```

Find the old state you want.

Create a recovery branch there:

```console
$ git branch rescue-branch <sha>
```

Or reset back if that is truly what you want:

```console
$ git reset --hard <sha>
```

Use destructive reset carefully. This repository's rules explicitly warn against casual destructive Git commands (`AGENTS.md:232-236`).

## Undoing things

Unstage a file:

```console
$ git restore --staged path/to/file
```

Typical output:

```text
(no output)
```

Restore one file from the last commit:

```console
$ git restore path/to/file
```

Undo the last commit but keep changes:

```console
$ git reset --soft HEAD~1
```

Undo a pushed commit safely with a new inverse commit:

```console
$ git revert <sha>
```

Typical output:

```text
[main fedcba9] Revert "add git guide chapter"
 1 file changed, 120 deletions(-)
```

Practical rule:

- use `revert` when history is already shared
- use `reset` when you are still cleaning up your own local branch

## Interactive rebase

Interactive rebase is for rewriting a branch's recent history in a controlled way.

Start it:

```console
$ git rebase -i HEAD~5
```

Common actions:

- `pick`
- `reword`
- `squash`
- `fixup`
- `drop`

Typical use cases:

- combine "oops" commits
- rewrite commit messages
- clean up noisy history before a PR

This repository explicitly tells contributors to clean up history before opening a PR (`CONTRIBUTING.md:342-343`).

## Team scenario: reviewer asked for three tiny fixes

You made three small follow-up commits:

- fix typo
- add missing test
- rename variable

Before the branch is merged, you may want to clean that up with interactive rebase:

```console
$ git rebase -i HEAD~4
```

Then squash or fixup the noisy commits.

## Cherry-pick

Cherry-pick copies one specific commit onto your current branch.

```console
$ git cherry-pick <sha>
```

### Team scenario: backport one hotfix

Your teammate fixed a bug on `feature-x`, but you only want one commit on `main` or `release`.

```console
$ git switch main
$ git cherry-pick <sha>
```

That is much safer than merging the entire branch if the rest is not ready.

## Searching history

Show history graph:

```console
$ git log --oneline --graph --decorate
```

Show history for one file:

```console
$ git log -- path/to/file
```

Follow renames:

```console
$ git log --follow -- path/to/file
```

Search commits by text pattern:

```console
$ git log -G "upload_file"
```

Show who changed each line:

```console
$ git blame path/to/file
```

## Bisect

`git bisect` finds the commit that introduced a bug by repeatedly cutting the search space in half.

Start:

```console
$ git bisect start
$ git bisect bad
$ git bisect good <old-good-sha>
```

If the checked-out commit is bad:

```console
$ git bisect bad
```

If it is good:

```console
$ git bisect good
```

Finish:

```console
$ git bisect reset
```

### Team scenario: a bug appeared sometime last week

You know the app worked five days ago and is broken now.

That is a classic bisect case.

## Tags

List tags:

```console
$ git tag
```

Create a tag:

```console
$ git tag v1.0.0
```

Annotated tag:

```console
$ git tag -a v1.0.0 -m "release 1.0.0"
```

Push one tag:

```console
$ git push origin v1.0.0
```

Push all tags:

```console
$ git push origin --tags
```

## Remotes

List remotes:

```console
$ git remote -v
```

Add a remote:

```console
$ git remote add origin git@github.com:OWNER/REPO.git
```

Fetch from a remote:

```console
$ git fetch origin
```

Pull from a remote:

```console
$ git pull origin main
```

Push to a remote:

```console
$ git push origin main
```

Set upstream while pushing:

```console
$ git push -u origin docs/git-chapter
```

Important distinction:

- `fetch` updates your knowledge of the remote
- `pull` usually fetches and then merges or rebases
- `push` sends your commits upward

## Team scenario: someone force-pushed the branch you were on

This is one of the messier real-world cases.

You pull and things look strange. Your local branch no longer matches the remote history shape.

Good first steps:

```console
$ git fetch origin
$ git status
$ git log --oneline --graph --decorate --all
$ git reflog
```

Do not panic and do not immediately hard reset unless you understand what happened.

Often the safest move is to create a temporary safety branch first:

```console
$ git branch rescue-before-sync
```

Then reconcile deliberately.

## Team scenario: you accidentally committed a secret

First, rotate the secret in the real service.

Then stop the file from being tracked if appropriate:

```console
$ git rm --cached .env
```

Then decide whether the history rewrite is local-only or already shared.

If already shared, treat it as an incident, not just a Git cleanup.

This repository explicitly forbids committing secrets (`AGENTS.md:232-236`).

## A simple Git cheatsheet

### Start and inspect

```console
$ git init
$ git clone <url>
$ git status
$ git status --short
$ git log --oneline --graph --decorate
```

### Stage and commit

```console
$ git add <file>
$ git add .
$ git add -p
$ git commit -m "message"
$ git commit --amend
```

### Diff and inspect

```console
$ git diff
$ git diff --staged
$ git show HEAD
$ git log -- path/to/file
$ git blame path/to/file
```

### Branches

```console
$ git switch <branch>
$ git switch -c <branch>
$ git branch
$ git branch -d <branch>
```

### Remote work

```console
$ git fetch origin
$ git pull origin main
$ git push origin main
$ git push -u origin <branch>
```

### Combine history

```console
$ git merge other-branch
$ git rebase main
$ git cherry-pick <sha>
```

### Recovery

```console
$ git stash
$ git stash pop
$ git reflog
$ git restore <file>
$ git restore --staged <file>
$ git revert <sha>
```

### Worktrees

```console
$ git worktree add ../path branch-name
$ git worktree list
$ git worktree remove ../path
```

## Git in this repository specifically

From `CONTRIBUTING.md:314-377` and `AGENTS.md:213-236`:

- branch from `main`
- use short descriptive branch names
- write imperative commit messages
- keep commits focused
- run checks before pushing
- open a PR instead of pushing directly to shared protected flow
- do not merge your own PR without the expected review flow
- do not commit secrets
- avoid destructive Git commands casually

## Further reading

- Pro Git book: <https://git-scm.com/book/en/v2>
- Git cheat sheet: <https://git-scm.com/cheat-sheet>
- Pro Git source: <https://github.com/progit/progit2>
- Frontend Masters Git course: <https://frontendmasters.com/courses/everything-git/>
- `CONTRIBUTING.md:312-377`
- `AGENTS.md:211-236`
