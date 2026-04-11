# Terminal Emulators

Before you learn shell commands, you should understand the window you are typing them into. A terminal emulator is the outermost layer of your terminal workflow. It draws text, handles fonts and colors, accepts keyboard input, and emulates the behavior of older physical terminals.

For this guide, I recommend **Ghostty**. The Ghostty docs describe Ghostty as a fast, feature-rich, cross-platform terminal emulator with platform-native UI and GPU acceleration (<https://ghostty.org/docs>). That is a strong modern baseline.

The important thing is not to turn your terminal emulator into your whole workflow. I do not recommend relying heavily on terminal-emulator-specific tabs and splits. Instead, I recommend Ghostty as the front-end and `tmux` as the layout and session layer. That gives you a setup that works locally, over SSH, and across machines.

## What to configure first

Do not begin by hunting for the perfect screenshot setup. Start with the things that affect your hands and eyes every day:

- use a readable monospace font,
- choose a high-contrast theme,
- make sure copy and paste feel natural,
- make sure modifier keys behave the way you expect,
- verify true color and Unicode rendering.

If you do only those things, you already have a good terminal.

## Why this matters in this repository

This repository expects you to spend real time in the terminal. Setup uses `uv sync --all-packages` (`README.md:103-111`). Testing uses `uv run pytest`, lint uses `uv run ruff check .`, types use `uv run mypy --strict .`, and the local service uses `uv run uvicorn aws_client_service.main:app --reload` (`AGENTS.md:72-129`). A frustrating terminal makes all of those tasks feel worse.

## Recommended stack

For this repository, I recommend this stack:

1. Ghostty as the terminal emulator.
2. `tmux` for session, window, and pane management.
3. `zsh` or `bash` as the shell.
4. Neovim as the editor.
5. CLI tools such as `uv`, `rg`, `fd`, `gh`, Docker, and Lazygit.
6. AI agents when they are useful.

That separation keeps each layer understandable.

## Further reading

- Ghostty docs: <https://ghostty.org/docs>
