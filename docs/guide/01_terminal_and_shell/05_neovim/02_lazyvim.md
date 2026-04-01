# LazyVim

This section is the practical LazyVim and plugin manual.

## Base idea

LazyVim gives you a preconfigured Neovim setup powered by `lazy.nvim` and `which-key.nvim`.

The most important thing to know first:

```text
leader key = <space>
```

Pressing `<space>` reveals discoverable actions.

## Recommended structure

Treat your setup in layers:

1. Vim motions and editing grammar
2. LazyVim core keymaps
3. search and navigation layer
4. diagnostics and LSP layer
5. Git layer
6. project-jump and working-set layer
7. terminal and session layer

That order matters because the plugins help most when they are extending a solid base, not replacing it.

## Core search and file actions

```text
<leader><space>   find files (root dir)
<leader>/         grep (root dir)
<leader>ff        find files
<leader>fF        find files (cwd)
<leader>fg        git files
<leader>fr        recent files
<leader>fp        projects
<leader>,         buffers
<leader>e         explorer (root dir)
<leader>E         explorer (cwd)
<leader>fb        buffers
<leader>fB        all buffers
<leader>fc        config file
```

Daily use pattern:

```text
find file
grep string
jump to buffers
open explorer when you need directory context
```

## Buffer, window, and tab actions

```text
<S-h>             previous buffer
<S-l>             next buffer
[b / ]b           previous / next buffer
<leader>bb        switch to other buffer
<leader>bd        delete buffer
<leader>bo        delete other buffers
<leader>-         split below
<leader>|         split right
<leader>wd        delete window
<C-h/j/k/l>       move between windows
<C-Left/Right>    resize width
<C-Up/Down>       resize height

<leader><tab><tab>   new tab
<leader><tab>]       next tab
<leader><tab>[       previous tab
<leader><tab>d       close tab
<leader><tab>o       close other tabs
```

## LSP actions

```text
gd              goto definition
gr              references
gI              implementation
gy              type definition
gD              declaration
K               hover
<leader>ca      code action
<leader>cr      rename symbol
<leader>co      organize imports
<leader>ss      document symbols
<leader>sS      workspace symbols
```

## Diagnostics and formatting

```text
<leader>cf      format
<leader>cd      line diagnostics
]d / [d         next / previous diagnostic
]e / [e         next / previous error
]w / [w         next / previous warning
<leader>xq      quickfix list
<leader>xl      location list
<leader>xx      diagnostics view
<leader>xX      buffer diagnostics view
```

## Terminal, sessions, and utility toggles

```text
<leader>ft      terminal (root dir)
<leader>fT      terminal (cwd)
<c-/>           terminal toggle
<leader>ql      restore last session
<leader>qs      restore session
<leader>qS      select session

<leader>uf      toggle auto format
<leader>us      toggle spelling
<leader>uw      toggle wrap
<leader>uL      toggle relative numbers
<leader>ud      toggle diagnostics
<leader>ul      toggle line numbers
<leader>uT      toggle Treesitter highlight
<leader>uz      toggle zen mode
```

## Git actions

```text
<leader>gb      blame line
<leader>gf      current file history
<leader>gl      git log
<leader>gL      git log (cwd)
<leader>gB      open browser URL
<leader>gY      copy browser URL
<leader>gs      git status
<leader>gS      git stash
<leader>gp      GitHub PRs (open)
<leader>gP      GitHub PRs (all)
<leader>gi      GitHub issues (open)
<leader>gI      GitHub issues (all)
```

## Plugin cheatsheet

### `which-key.nvim`

```text
<leader>?       buffer keymaps
<space>         open leader-key menu
```

### `flash.nvim`

```text
s               flash jump
S               treesitter flash
r               remote flash
R               treesitter search
```

Use this when you want to jump quickly on the current screen without doing a full search.

### `mini.surround`

```text
gsa             add surrounding
gsd             delete surrounding
gsr             replace surrounding
gsh             highlight surrounding
```

Examples:

```text
gsa"            surround with double quotes
gsd"            delete surrounding quotes
gsr"'           replace double quotes with single quotes
```

### `mini.ai`

Use text objects more flexibly inside functions, arguments, quotes, brackets, and other structures.

Typical pattern:

```text
vai             select around object
vii             select inside object
```

Use this to make text-object editing broader and more flexible.

### `trouble.nvim`

```text
<leader>xx      diagnostics
<leader>xX      buffer diagnostics
<leader>xQ      quickfix list
<leader>xL      location list
<leader>cs      symbols
<leader>cS      references/definitions/etc
```

Use Trouble when you want a dedicated panel for navigation through diagnostics or symbol references.

### `todo-comments.nvim`

```text
<leader>st      todo list
<leader>sT      todo/fix/fixme
[t              previous todo
]t              next todo
```

### `noice.nvim`

```text
<leader>snh     message history
<leader>snl     last message
<leader>snd     dismiss messages
```

### `yanky.nvim`

```text
<leader>p       yank history
[y / ]y         cycle yank history
gp / gP         put after / before selection
```

This is useful when your yank history matters and you want to recover older copies without re-yanking.

### `mason.nvim`

```text
<leader>cm      Mason
```

### `conform.nvim`

```text
<leader>cf      format
<leader>cF      format injected languages
```

### `snacks.nvim` finder actions

```text
<leader>sb      buffer lines
<leader>sB      grep open buffers
<leader>sd      diagnostics
<leader>sD      buffer diagnostics
<leader>sg      grep root
<leader>sG      grep cwd
<leader>sh      help pages
<leader>sj      jumps
<leader>sk      keymaps
<leader>sl      location list
<leader>sm      marks
<leader>sq      quickfix list
<leader>sR      resume picker
<leader>sw      search word/selection in root
<leader>sW      search word/selection in cwd
```

This is the core search/navigation layer in a LazyVim-heavy workflow.

## Additional plugins and workflows from fast Neovim setups

These are not all LazyVim defaults, but they are worth learning because they show up repeatedly in strong terminal-first Neovim setups.

### `telescope.nvim`

Use Telescope as the main fuzzy-finder UI.

Typical actions:

```text
find files
git files
live grep
grep string under cursor
buffer fuzzy search
help tags
recent files
```

Recommended mappings if you add or customize it:

```text
<leader>pf      find files
<C-p>           git files
<leader>ps      grep prompt
<leader>pws     grep word under cursor
<leader>/       fuzzy search current buffer
```

Good companion extensions:

- `telescope-fzf-native.nvim`
- `telescope-smart-history.nvim`
- `telescope-ui-select.nvim`

### `vim-fugitive`

Use Fugitive for Git inside the editor.

Common commands:

```text
:Git
:Gvdiffsplit
:Git push
:Git pull --rebase
:Git blame
```

Recommended mappings if you want a fast Git layer:

```text
<leader>gs      open Fugitive status
<leader>gp      Git push
<leader>gP      Git pull --rebase
<leader>gt      Git push -u origin <current-branch>
```

Conflict resolution helpers often paired with Fugitive-style workflows:

```text
:diffget //2
:diffget //3
```

### `undotree`

Use Undotree to inspect and jump around undo history.

Common command:

```text
:UndotreeToggle
```

Recommended mapping:

```text
<leader>u       toggle undotree
```

### `zen-mode.nvim`

Use Zen Mode when you want a quieter writing or coding view.

Common command:

```text
:ZenMode
```

Recommended mapping:

```text
<leader>zz      toggle zen mode
```

### `harpoon`

Harpoon is for keeping a small working set of files one jump away.

Typical workflow:

1. mark a file
2. open the quick menu
3. jump directly to marks 1, 2, 3, 4, 5
4. cycle through the marks

Recommended mapping set:

```text
<leader>a       add file to Harpoon
<C-e>           toggle Harpoon quick menu
<C-h>           jump to mark 1
<C-t>           jump to mark 2
<C-n>           jump to mark 3
<C-s>           jump to mark 4
```

If you prefer numeric mappings, use 1..5 under a leader key. The point is not the exact keys. The point is that your active working set is always one jump away.

A good Harpoon list for this repo might be:

- `pyproject.toml:15-125`
- `AGENTS.md:58-129`
- `src/aws_client_service/aws_client_service/main.py:89-199`
- `src/cloud_storage_client_api/cloud_storage_client_api/client.py:14-141`
- the docs file you are editing

### `aerial.nvim`

Aerial gives you an outline view of symbols.

Typical commands:

```text
:AerialToggle
:AerialNavToggle
```

Recommended mappings:

```text
<leader>aa      toggle Aerial
{               previous symbol
}               next symbol
```

Typical navigation when configured:

```text
{               previous symbol
}               next symbol
```

### `oil.nvim`

Oil turns directory editing into a buffer-based workflow.

Typical usage:

```text
-               open parent directory
<space>-        open floating oil view
```

Use Oil when you want a directory editor instead of a tree sidebar.

### Built-in terminal split workflow

A simple fast setup often uses the built-in terminal instead of a heavy terminal plugin.

Open a terminal split, run tests, and return to editing.

Recommended mapping pattern:

```text
,st             open bottom terminal split
<Esc><Esc>      leave terminal mode
```

### tmux-sessionizer style launcher

Some fast configs bind Neovim to a `tmux` session picker or project switcher. The pattern matters more than the exact script:

- one keybinding opens a project/session switcher,
- fuzzy-pick a repo,
- jump into the right terminal/editor workspace.

Recommended mapping pattern:

```text
<C-f>           open sessionizer or project picker
```

This is one of the highest-leverage project-switching workflows once you work across many repositories.

### `trouble.nvim` plus quickfix plus grep

A very strong code-navigation pattern is:

1. search with Telescope or `rg`
2. populate quickfix or Trouble
3. walk the results rapidly

This is especially effective for repository-wide refactors.

### `flash.nvim` plus motions

Use Flash to reduce on-screen travel. Use Vim motions for local structure. Use Harpoon and Telescope for larger jumps. That three-layer navigation model scales well.

### `fidget.nvim`

`fidget.nvim` is useful as a lightweight LSP progress/status indicator. It keeps long LSP actions visible without becoming noisy.

### `conform.nvim` plus save discipline

Use `conform.nvim` as the formatting layer and keep the format command very close to your hands.

For this repository, formatting and linting still belong in the terminal too:

```console
$ uv run ruff format .
$ uv run ruff check .
```

## A practical Neovim loop for this repo

1. Open Neovim in the repo root.
2. Use file finding to open the core files.
3. Pin the important files with Harpoon.
4. Use Aerial on long files.
5. Use grep and quickfix for project-wide navigation.
6. Use the built-in terminal or `tmux` for verification commands.
7. Open Lazygit or Fugitive when you need Git context.

Good files to keep close:

- `README.md:103-149`
- `AGENTS.md:58-129`
- `pyproject.toml:15-125`
- `src/cloud_storage_client_api/cloud_storage_client_api/client.py:14-141`
- `src/aws_client_service/aws_client_service/main.py:77-281`
- `docs/source/curl-tutorial.md:12-104`

## Suggested minimum plugin stack

If you want a strong but not bloated setup, start with:

- LazyVim base
- Telescope or the default LazyVim finder layer
- Treesitter
- LSP + Mason
- Conform
- Trouble
- Harpoon
- Aerial
- mini.surround
- mini.ai
- Lazygit or Fugitive

That is enough to build a very fast editing workflow without drowning in plugin complexity.
