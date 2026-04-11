# tmux

If the shell is the language of the terminal, `tmux` is the architecture of the terminal.

It gives you three core ideas:

- **sessions** for long-lived workspaces,
- **windows** for tab-like groupings,
- **panes** for splits inside windows.

## Install tmux

On macOS with Homebrew:

```console
$ brew install tmux
```

On Ubuntu or Debian:

```console
$ sudo apt update
$ sudo apt install tmux
```

Check the version:

```console
$ tmux -V
```

## Start using tmux

Start a new session:

```console
$ tmux
```

Start a named session:

```console
$ tmux new -s ospsd
```

Attach to a named session:

```console
$ tmux attach -t ospsd
```

List sessions:

```console
$ tmux ls
```

Kill a session:

```console
$ tmux kill-session -t ospsd
```

Detach from a session:

```text
Prefix d
```

## Config location

Use this path:

```text
~/.config/tmux/tmux.conf
```

Create the directory and file:

```console
$ mkdir -p ~/.config/tmux
$ nvim ~/.config/tmux/tmux.conf
```

`tmux` gives you a consistent, programmable interface for panes, windows, and resumable sessions that also works on remote hosts. That is exactly why it matters.

## Why use tmux here

`tmux` is not just about splitting the screen. Its real value is persistence and transferability. You can close a terminal and come back. You can SSH into a server and keep the same layout habits. You can separate your terminal emulator choice from your session model.

## Recommended config location

```text
~/.config/tmux/tmux.conf
```

## Your config

Below is the config you shared in this session, captured as the basis for this guide:

```text
# Prefix
set -g prefix C-Space
set -g prefix2 C-b
bind C-Space send-prefix

# Clear screen
bind C-l send-keys 'C-l'

# Reload config
bind q source-file ~/.config/tmux/tmux.conf \; display "Configuration reloaded"

# Vi mode for copy
setw -g mode-keys vi
bind -T copy-mode-vi v send -X begin-selection
bind -T copy-mode-vi y send -X copy-selection-and-cancel

# Pane Controls
bind h split-window -v -c "#{pane_current_path}"
bind v split-window -h -c "#{pane_current_path}"
bind x kill-pane

bind -n C-M-Left select-pane -L
bind -n C-M-Right select-pane -R
bind -n C-M-Up select-pane -U
bind -n C-M-Down select-pane -D

bind -n C-M-S-Left resize-pane -L 5
bind -n C-M-S-Down resize-pane -D 5
bind -n C-M-S-Up resize-pane -U 5
bind -n C-M-S-Right resize-pane -R 5

# Window navigation
bind r command-prompt -I "#W" "rename-window -- '%%'"
bind c new-window -c "#{pane_current_path}"
bind k kill-window

bind -n M-1 select-window -t 1
bind -n M-2 select-window -t 2
bind -n M-3 select-window -t 3
bind -n M-4 select-window -t 4
bind -n M-5 select-window -t 5
bind -n M-6 select-window -t 6
bind -n M-7 select-window -t 7
bind -n M-8 select-window -t 8
bind -n M-9 select-window -t 9

bind -n M-Left select-window -t -1
bind -n M-Right select-window -t +1
bind -n M-S-Left swap-window -t -1 \; select-window -t -1
bind -n M-S-Right swap-window -t +1 \; select-window -t +1

# Session controls
bind R command-prompt -I "#S" "rename-session -- '%%'"
bind C new-session -c "#{pane_current_path}"
bind K kill-session
bind P switch-client -p
bind N switch-client -n

bind -n M-Up switch-client -p
```

## Why this config is strong

This config has a practical philosophy:

- a comfortable custom prefix with a fallback,
- vi-style copy mode,
- new panes and windows inheriting the current working directory,
- navigation bindings that do not require the prefix all the time,
- fast switching among windows and sessions.

That is the kind of setup that grows with you.

## How to load the config

From inside tmux:

```text
Prefix q
```

Or from the shell:

```console
$ tmux source-file ~/.config/tmux/tmux.conf
```

## Basic tmux commands worth knowing first

### Sessions

```text
tmux new -s name          create named session
tmux attach -t name       attach session
tmux ls                   list sessions
tmux kill-session -t name kill session
```

### Windows

```text
Prefix c                  new window
Prefix ,                  rename window in default tmux
Prefix &                  kill window in default tmux
```

In your config, use:

```text
Prefix c                  new window in current path
Prefix r                  rename window
Prefix k                  kill window
```

### Panes

```text
Prefix %                  split vertically in default tmux
Prefix "                  split horizontally in default tmux
Prefix x                  kill pane
```

In your config, use:

```text
Prefix h                  split below in current path
Prefix v                  split right in current path
Prefix x                  kill pane
```

## Cheat sheet

### Prefix and reload

```text
Ctrl-Space     Prefix
Prefix q       Reload config
Ctrl-l         Clear screen
```

### Panes

```text
Prefix h       Split horizontally
Prefix v       Split vertically
Prefix x       Kill pane

Ctrl-Alt-Left   Move left
Ctrl-Alt-Right  Move right
Ctrl-Alt-Up     Move up
Ctrl-Alt-Down   Move down

Ctrl-Alt-Shift-Left   Resize left
Ctrl-Alt-Shift-Right  Resize right
Ctrl-Alt-Shift-Up     Resize up
Ctrl-Alt-Shift-Down   Resize down
```

### Windows

```text
Prefix c       New window
Prefix r       Rename window
Prefix k       Kill window
Alt-1..9       Jump to window
Alt-Left       Previous window
Alt-Right      Next window
Alt-Shift-Left   Move window left
Alt-Shift-Right  Move window right
```

### Sessions

```text
Prefix C       New session
Prefix R       Rename session
Prefix K       Kill session
Prefix P       Previous session
Prefix N       Next session
Alt-Up         Previous session
```

## A good layout for this repo

For this repository, a very good working layout is:

- left pane: editor
- top-right pane: tests or docs build
- bottom-right pane: shell commands, Git, or an AI agent

That lines up nicely with commands the repo expects you to run often:

```console
$ uv run pytest -q
$ uv run sphinx-autobuild docs/source docs/build/html
$ uv run uvicorn aws_client_service.main:app --reload
```

## Further reading

- `man tmux`
