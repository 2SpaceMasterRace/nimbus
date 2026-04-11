# Terminal and Shell

This chapter is about building a terminal-first development environment that you can actually live in. Instead of treating the shell as a random list of commands and shortcuts, we are going to treat it as a workspace with layers: a terminal emulator, a shell, better command-line tools, a multiplexer, an editor, version control helpers, containers, and AI agents.

This repository expects that kind of workflow. Setup is terminal-driven, testing is terminal-driven, docs are terminal-driven, and the service is run from the terminal (`AGENTS.md:58-129`, `README.md:103-149`, `CONTRIBUTING.md:79-87`, `181-260`). So this chapter is not optional background. It is part of learning how to work on the project at all.

In this chapter, we will move from the outside in. We will start with the terminal emulator, then the shell, then the modern tools that make the shell feel powerful, then `tmux`, then Neovim, then Docker, then GitHub CLI and Lazygit, and finally AI tooling and agent workflows.

```{toctree}
:maxdepth: 1

01_terminal_emulators
02_shell_fundamentals
03_shell_tools
04_tmux
05_neovim/index
06_docker
07_github_cli_and_lazygit
08_ai_agents
```
