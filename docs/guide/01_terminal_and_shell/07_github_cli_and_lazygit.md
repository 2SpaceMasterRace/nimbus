# GitHub CLI and Lazygit

This section is a manual for using `gh` and Lazygit.

## GitHub CLI

### Authentication

Login:

```console
$ gh auth login
```

Check auth status:

```console
$ gh auth status
```

Logout:

```console
$ gh auth logout
```

Set up Git to use GitHub credentials:

```console
$ gh auth setup-git
```

### Set a default repo

If you are inside a clone and want `gh` commands to target that repo by default:

```console
$ gh repo set-default OWNER/REPO
```

Show the current repo:

```console
$ gh repo view
```

### Repository commands

Clone a repository:

```console
$ gh repo clone OWNER/REPO
```

Fork a repository:

```console
$ gh repo fork OWNER/REPO
```

Open the repo in a browser:

```console
$ gh repo view --web
```

### Issue commands

List issues:

```console
$ gh issue list
```

View one issue:

```console
$ gh issue view 123
```

Create an issue:

```console
$ gh issue create
```

Close an issue:

```console
$ gh issue close 123
```

### Pull request commands

List PRs:

```console
$ gh pr list
```

View a PR:

```console
$ gh pr view 10
```

View PR checks:

```console
$ gh pr checks 10
```

Create a PR:

```console
$ gh pr create
```

Edit a PR:

```console
$ gh pr edit 10
```

Check out a PR locally:

```console
$ gh pr checkout 10
```

Merge a PR:

```console
$ gh pr merge 10
```

Show the diff for a PR:

```console
$ gh pr diff 10
```

Comment on a PR:

```console
$ gh pr comment 10 --body "Looks good"
```

Mark a PR ready for review:

```console
$ gh pr ready 10
```

Merge with squash:

```console
$ gh pr merge 10 --squash
```

Merge with rebase:

```console
$ gh pr merge 10 --rebase
```

Open PR in browser:

```console
$ gh pr view 10 --web
```

### Workflow and Actions

List workflow runs:

```console
$ gh run list
```

Watch a workflow:

```console
$ gh run watch
```

View a specific run:

```console
$ gh run view RUN_ID
```

Download logs:

```console
$ gh run view RUN_ID --log
```

Show only failed logs:

```console
$ gh run view RUN_ID --log-failed
```

Rerun a job or workflow:

```console
$ gh run rerun RUN_ID
```

### API mode

Raw GitHub API request:

```console
$ gh api repos/OWNER/REPO/pulls/10
```

JSON shaping with `jq`:

```console
$ gh pr view 10 --json title,url,commits | jq .
```

Extract a single field:

```console
$ gh pr view 10 --json title | jq -r .title
```

List PR numbers and titles:

```console
$ gh pr list --json number,title | jq -r '.[] | "#\(.number) \(.title)"'
```

List workflow runs as JSON:

```console
$ gh run list --json databaseId,workflowName,status,conclusion | jq .
```

### Practical repo examples

```console
$ gh auth status
$ gh pr view 10
$ gh pr diff 10
$ gh pr checks 10
$ gh pr view 10 --json title,commits,url | jq .
$ gh run watch
```

## Common `gh` workflows

### Open a PR from your branch

```console
$ git push -u origin my-branch
$ gh pr create
```

### Inspect CI after creating the PR

```console
$ gh pr checks 10
$ gh run watch
```

### Review a PR from the terminal

```console
$ gh pr view 10
$ gh pr diff 10
$ gh pr comment 10 --body "Please rename this function"
```

### Pull GitHub data into shell scripts

```console
$ gh pr list --json number,title,url | jq .
$ gh issue list --json number,title,labels | jq .
```

## Lazygit

Start Lazygit inside a Git repo:

```console
$ lazygit
```

## Lazygit cheat sheet

Legend:

- `<c-x>` means Ctrl+x
- `<a-x>` means Alt+x
- uppercase means Shift

### Global

```text
q          quit
?          keybindings menu
R          refresh
p          pull
P          push
:          execute shell command
m          merge/rebase options
z          undo
Z          redo
<esc>      cancel
```

### Recommended first panels to learn

Learn these in order:

1. Files
2. Commit diff/staging view
3. Branches
4. Commits
5. Stash
6. Worktrees

### Navigation

```text
,          previous page
.          next page
<home>     top
<end>      bottom
/          filter current view
[          previous tab
]          next tab
```

### Files panel

```text
<space>    stage / unstage file
a          stage all
c          commit staged changes
C          commit with git editor
A          amend last commit
d          discard file changes
D          reset / nuke working tree options
s          stash all
S          stash options
f          fetch
e          edit file in external editor
o          open file in default app
<enter>    stage lines / enter directory
`          toggle tree view
-          collapse all directories
=          expand all directories
```

Typical file-panel flow:

```text
move to a file
press Space to stage it
press Enter to inspect diff or hunks
press c to commit staged work
press P to push
```

### Main panel while staging

```text
<space>    stage selected hunk/lines
d          discard or unstage selected hunk
v          range select
a          toggle hunk selection mode
<left>     previous hunk
<right>    next hunk
E          edit hunk
<tab>      switch staged/unstaged view
<esc>      return to files panel
```

### Commits panel

```text
<enter>    view commit files
s          squash into commit below
f          fixup into commit below
r          reword commit
R          reword in editor
d          drop commit
e          edit commit / start interactive rebase from commit
i          start interactive rebase
p          mark commit as pick during rebase
F          create fixup commit
S          apply fixup commits / autosquash
<c-j>      move commit down
<c-k>      move commit up
A          amend selected commit with staged changes
t          revert commit
T          tag commit
n          create branch from commit
C          copy commit for cherry-pick
V          paste copied commit (cherry-pick)
B          mark base commit for rebase --onto
g          reset to selected commit options
o          open commit in browser
y          copy commit info
```

This is the panel to learn when you start doing cleanup work, autosquash fixups, cherry-picks, and careful history edits.

### Branches panel

```text
<space>    checkout branch
n          new branch
N          move commits to new branch
o          create pull request
O          PR options
<c-y>      copy PR URL
c          checkout by name
-          previous branch
F          force checkout
d          delete branch
r          rebase current branch onto selected branch
M          merge selected branch into current branch
f          fast-forward from upstream
R          rename branch
u          upstream options
T          new tag
w          worktree options
```

Very common branch flow:

```text
open branches panel
press n to create branch
work normally
press o to create PR through GitHub integration
```

### Remotes panel

```text
<enter>    view remote branches
n          new remote
d          remove remote
e          edit remote
f          fetch remote
F          add fork remote
```

### Remote branches panel

```text
<space>    checkout remote branch
n          new branch from remote
M          merge
r          rebase onto selected remote branch
d          delete remote branch
u          set as upstream
```

### Stash panel

```text
<space>    apply stash
g          pop stash
d          drop stash
n          new branch from stash
r          rename stash
<enter>    view stash files
```

### Tags panel

```text
<space>    checkout tag
n          create tag
d          delete tag
P          push tag
<enter>    view commits
```

### Worktrees panel

```text
n          new worktree
<space>    switch to worktree
o          open worktree in editor
d          remove worktree
```

This is especially useful when reviewing another branch without disturbing your current working tree.

### Merge conflict view

```text
<space>    pick hunk
b          pick all hunks
<up>       previous hunk
<down>     next hunk
<left>     previous conflict
<right>    next conflict
z          undo merge resolution
e          edit file
o          open file
M          merge conflict options
```

### Status and repo switching

```text
<enter>    switch to recent repo
a          cycle branch logs
A          cycle branch logs reverse
u          check for update
```

## Common repo workflow with `gh` and Lazygit

Open Lazygit:

```console
$ lazygit
```

Stage work, commit, inspect branches, push, then use `gh`:

```console
$ gh pr create
$ gh pr view 10
$ gh pr diff 10
$ gh pr checks 10
$ gh run watch
```

## Common terminal workflow with both tools

1. open `lazygit`
2. stage and commit changes
3. push the branch
4. create the PR with `gh`
5. inspect CI with `gh`

Example:

```console
$ lazygit
$ git push -u origin my-branch
$ gh pr create
$ gh pr checks 10
$ gh run watch
```

## Further reading

- GitHub CLI manual: <https://cli.github.com/manual/>
- Lazygit keybindings: <https://raw.githubusercontent.com/jesseduffield/lazygit/master/docs/keybindings/Keybindings_en.md>
