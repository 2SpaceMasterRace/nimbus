# Shell Tools

This section is a how-to guide for a modern shell toolkit.

## `zsh`

### Check whether `zsh` is installed

```console
$ zsh --version
```

### Install `zsh`

On macOS with Homebrew:

```console
$ brew install zsh
```

On Ubuntu or Debian:

```console
$ sudo apt update
$ sudo apt install zsh
```

### Make `zsh` your default shell

```console
$ chsh -s $(which zsh)
```

Then log out and log back in.

### Create a basic `~/.zshrc`

```zsh
export EDITOR=nvim
export VISUAL=nvim

alias ll='ls -la'
alias gs='git status'
alias v='nvim'
```

Reload it:

```console
$ source ~/.zshrc
```

## Oh My Zsh

Oh My Zsh is a framework for managing your `zsh` configuration and plugin setup.

### Install Oh My Zsh

```console
$ sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
```

Or with `wget`:

```console
$ sh -c "$(wget https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh -O -)"
```

### Basic Oh My Zsh config

Open `~/.zshrc` and set:

```zsh
export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="robbyrussell"
plugins=(git)

source $ZSH/oh-my-zsh.sh
```

### A practical plugin list

For a developer shell, a good starting plugin list is:

```zsh
plugins=(git docker python uv)
```

If a plugin is not bundled, install it manually and source it or add it through `$ZSH_CUSTOM`.

## Recommended `zsh` plugins

These are the plugins I recommend first:

- `zsh-autosuggestions`
- `zsh-syntax-highlighting`
- `fzf-tab`
- `zoxide` integration

### `zsh-autosuggestions`

This gives fish-like command suggestions based on history and completion.

Install it into Oh My Zsh custom plugins:

```console
$ git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-autosuggestions
```

Add it to the plugin list in `~/.zshrc`:

```zsh
plugins=(git docker python uv zsh-autosuggestions)
```

Reload:

```console
$ source ~/.zshrc
```

Usage:

- type a command prefix
- see a gray suggestion
- press Right Arrow or End to accept it

Optional style config:

```zsh
ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE="fg=8"
```

### `zsh-syntax-highlighting`

This highlights commands as you type, which helps catch mistakes before pressing Enter.

Install it:

```console
$ git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting
```

Add it to `plugins=(...)`:

```zsh
plugins=(git docker python uv zsh-autosuggestions zsh-syntax-highlighting)
```

Important: `zsh-syntax-highlighting` must be loaded near the end of your `~/.zshrc` so it can wrap the right widgets cleanly.

### `fzf-tab`

`fzf-tab` replaces the normal completion selection menu with `fzf`.

Install `fzf` first.

Then install the plugin:

```console
$ git clone https://github.com/Aloxaf/fzf-tab ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/fzf-tab
```

Add it to your plugin list. Place it before autosuggestions and syntax-highlighting if you hit widget conflicts:

```zsh
plugins=(git docker python uv fzf-tab zsh-autosuggestions zsh-syntax-highlighting)
```

Example config:

```zsh
zstyle ':completion:*' menu no
zstyle ':fzf-tab:*' switch-group '<' '>'
zstyle ':fzf-tab:complete:cd:*' fzf-preview 'eza -1 --color=always $realpath'
```

Usage:

- press Tab as usual
- fuzzy filter the completion list
- use `/` for continuous completion
- use `Ctrl-Space` to multi-select if configured

### `zoxide` in `zsh`

After installing `zoxide`, add this to `~/.zshrc`:

```zsh
eval "$(zoxide init zsh)"
```

Then reload and use:

```console
$ z ospsd
$ zi
```

## Recommended `~/.zshrc` starter example

```zsh
export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="robbyrussell"

plugins=(
  git
  docker
  python
  uv
  fzf-tab
  zsh-autosuggestions
  zsh-syntax-highlighting
)

export EDITOR=nvim
export VISUAL=nvim

alias ll='eza -la --git'
alias v='nvim'
alias gs='git status'
alias lg='lazygit'

source $ZSH/oh-my-zsh.sh

eval "$(zoxide init zsh)"

zstyle ':completion:*' menu no
zstyle ':fzf-tab:*' switch-group '<' '>'
zstyle ':fzf-tab:complete:cd:*' fzf-preview 'eza -1 --color=always $realpath'

ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE="fg=8"
```

## `fzf`

`fzf` is a fuzzy finder.

### Search shell history

In many shell setups, `Ctrl-r` opens fuzzy history search.

Type part of a previous command, filter, and press Enter.

### Pick from a list

```console
$ printf "%s\n" apple banana orange | fzf
```

### Open a file selected by `fzf`

```console
$ nvim $(fd . docs | fzf)
```

### Search Git-tracked files

```console
$ git ls-files | fzf
```

### Combine with preview

```console
$ fd . docs | fzf --preview 'bat --style=numbers --color=always {}'
```

## `zoxide`

`zoxide` is a smarter `cd`.

### Jump to a familiar directory

```console
$ z ospsd
```

### Interactive jump

```console
$ zi
```

### Add directories naturally

Just visit them. `zoxide` learns from usage.

## `ripgrep`

`ripgrep`, or `rg`, is the main modern text search tool.

### Search for text

```console
$ rg CloudStorageClient src tests
```

### Search hidden files too

```console
$ rg --hidden SESSION_SECRET_KEY .
```

### Search by file type

```console
$ rg "def main" -g '*.py'
```

### Show only matching file names

```console
$ rg -l TODO docs src
```

### Invert a match

```console
$ rg -L "pytestmark" tests
```

### Show context around matches

```console
$ rg -n -C 2 "upload_file" src
```

## `fd`

`fd` is a friendlier file finder.

### Find files by name fragment

```console
$ fd test_ src
```

### Find by extension

```console
$ fd -e md docs
```

### Limit to files or directories

```console
$ fd -t f pyproject
$ fd -t d guide docs
```

### Run a command on results

```console
$ fd -e md docs -x wc -l {}
```

## `eza`

`eza` is a better `ls`.

### Long listing

```console
$ eza -la
```

### Tree view

```console
$ eza --tree docs
```

### Show git status

```console
$ eza -la --git
```

## `bat`

If you install `bat`, use it as a nicer file viewer.

### View a file with syntax highlighting

```console
$ bat README.md
```

### View line numbers

```console
$ bat --style=numbers pyproject.toml
```

## `jq`

`jq` is for JSON.

### Pretty print JSON

```console
$ gh pr view 10 --json title,commits | jq .
```

### Pick one field

```console
$ gh pr view 10 --json title | jq -r .title
```

### Build a smaller JSON view

```console
$ gh pr view 10 --json title,url,author | jq '{title, url, author: .author.login}'
```

## `delta`

If you install `delta`, use it as a better diff viewer.

### View a colored diff

```console
$ git diff | delta
```

## `try`

If you use a disposable experiment helper such as `try`, the pattern is simple: test a command in an isolated space before you commit it to muscle memory or automation.

Even without a dedicated `try` tool, keep this habit:

- test a command on one file before many files,
- inspect output,
- then automate.

## Common combinations

### `fd` + `fzf`

```console
$ nvim $(fd . src | fzf)
```

### `rg` + `fzf`

```console
$ rg -l CloudStorageClient src tests | fzf
```

### `gh` + `jq`

```console
$ gh pr list --json number,title,url | jq .
```

### `docker` + `rg`

```console
$ docker ps | rg cloud-storage-service
```

## Suggested install set

If you want a very practical toolset, install:

- `zsh`
- Oh My Zsh
- `fzf`
- `zoxide`
- `ripgrep`
- `fd`
- `eza`
- `bat`
- `jq`
- `zsh-autosuggestions`
- `zsh-syntax-highlighting`
- `fzf-tab`

## Further reading

- Oh My Zsh: <https://ohmyz.sh/>
- zsh-autosuggestions: <https://github.com/zsh-users/zsh-autosuggestions>
- zsh-syntax-highlighting: <https://github.com/zsh-users/zsh-syntax-highlighting>
- fzf-tab: <https://github.com/Aloxaf/fzf-tab>
- `man rg`
- `man fd`
- `man jq`
- `fzf --help`
