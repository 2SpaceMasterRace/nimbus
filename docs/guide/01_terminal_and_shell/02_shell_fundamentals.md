# Shell Fundamentals

This section is a practical shell manual. It follows the broad learning arc of *The Linux Command Line*: what the shell is, navigating the filesystem, working with files, using commands, redirection, expansion, permissions, processes, environment variables, package management, networking, searching, text processing, archiving, and scripting.

The goal here is not to make you memorize everything in one sitting. The goal is to give you one place where the core shell ideas live, with examples you can type and adapt.

## Terminal, shell, and command line

A **terminal emulator** is the program window.

A **shell** is the program running inside the terminal.

A **command line** is the style of interacting with a program by typing commands.

Examples of shells:

- `bash`
- `zsh`
- `sh`
- `fish`

## Starting commands

Most commands look like this:

```console
$ command [options] [arguments]
```

Examples from this repository:

```console
$ uv sync --all-packages
$ uv run pytest
$ uv run uvicorn aws_client_service.main:app --reload
$ uv run sphinx-build docs/source docs/build/html
```

## Knowing where you are

```console
$ pwd
$ ls
$ ls -la
$ cd path
$ cd ..
$ cd ~
```

Example in this repo:

```console
$ cd ~/Work/ospsd-team-2
$ pwd
$ ls
```

## Looking around

```console
$ file README.md
$ less README.md
$ man uv
$ which python
$ which gh
```

Use `less` to read long text files one screen at a time.

Use `man` for the system manual.

Use `which` to see what executable your shell will run.

## Creating and manipulating files

```console
$ mkdir notes
$ touch scratch.txt
$ cp source.txt target.txt
$ mv old-name.txt new-name.txt
$ rm file.txt
$ rm -r directory/
```

Use `rm` carefully. The shell will not protect you from accidental deletion.

## Paths

Absolute path:

```text
/Users/nanodijkstra/Work/ospsd-team-2/README.md
```

Relative path:

```text
README.md
```

Relative from a subdirectory:

```text
../README.md
```

## Redirection

Write standard output to a file:

```console
$ command > out.txt
```

Append standard output:

```console
$ command >> out.txt
```

Write standard error:

```console
$ command 2> err.txt
```

Examples:

```console
$ uv run pytest -q > test-output.txt
$ uv run mypy --strict . 2> type-errors.txt
```

## Pipes

Pipes send one command's output into another command's input.

```console
$ command1 | command2
```

Examples:

```console
$ uv run pytest -q | less
$ gh pr view 10 --json title,commits | jq .
$ docker ps | rg cloud-storage-service
```

## Quoting

Single quotes are literal:

```console
$ echo '$HOME'
```

Double quotes expand variables:

```console
$ echo "$HOME"
```

Wildcards expand when unquoted:

```console
$ echo *.py
$ echo "*.py"
```

Rule: quote paths and values when they may contain spaces or special characters.

## Expansion

The shell performs expansions before running commands.

### Variable expansion

```console
$ export NAME="world"
$ echo "hello $NAME"
```

### Command substitution

```console
$ echo "today is $(date)"
```

### Wildcards

```console
$ ls *.md
$ ls src/**/*.py
```

### Brace expansion

```console
$ mkdir chapter-{1,2,3}
```

### Tilde expansion

```console
$ cd ~
```

## Environment variables

Set a variable:

```console
$ export API_KEY="replace-me"
$ export AWS_REGION="us-east-1"
```

Print a variable:

```console
$ echo "$AWS_REGION"
```

Load a `.env` file into the current shell:

```console
$ set -a && source .env && set +a
```

This repository uses environment variables heavily for AWS credentials, API keys, and OAuth settings (`README.md:115-130`, `README.md:266-285`, `AGENTS.md:194-207`).

## Exit codes

Every command exits with a status code.

- `0` means success
- nonzero means failure

See the last exit code:

```console
$ uv run pytest
$ echo $?
```

## Permissions

Show permissions:

```console
$ ls -l
```

Change permissions:

```console
$ chmod +x script.sh
```

Change ownership:

```console
$ chown user:group file.txt
```

Common case: making a script executable.

```console
$ chmod +x scripts/run_e2e_tests.sh
```

## Processes and job control

List processes:

```console
$ ps
$ pgrep uvicorn
```

Kill a process:

```console
$ kill <pid>
```

See shell jobs:

```console
$ jobs
```

Bring a background job to foreground:

```console
$ fg
```

Send a job to background:

```console
$ bg
```

Example with this repo:

```console
$ uv run uvicorn aws_client_service.main:app --reload
```

That command starts a long-running process.

## Help and documentation

```console
$ command --help
$ man command
$ apropos ssh
$ info bash
```

If a tool ships with `--help`, use it first.

## Package management

Different operating systems use different package managers.

### Homebrew

```console
$ brew install gh
$ brew install tmux
$ brew install lazygit
```

### apt

```console
$ sudo apt update
$ sudo apt install ripgrep fd-find jq
```

### uv for Python packages in this repo

```console
$ uv sync --all-packages
```

Do not use `pip install` directly in this project. The repo's instructions are explicit that dependency management goes through `uv` (`AGENTS.md:58-64`).

## Search tools

Classic search:

```console
$ grep -R "CloudStorageClient" .
```

Modern search:

```console
$ rg CloudStorageClient src tests
$ rg SESSION_SECRET_KEY .
```

Find files:

```console
$ find . -name '*.py'
$ fd test_ src
```

## Text processing

### Count lines, words, bytes

```console
$ wc README.md
```

### Sort and unique

```console
$ sort names.txt
$ sort names.txt | uniq
```

### Cut columns

```console
$ cut -d: -f1 /etc/passwd
```

### Sed replace

```console
$ sed 's/foo/bar/g' file.txt
```

### Awk field processing

```console
$ awk '{print $1}' file.txt
```

### Xargs

```console
$ rg -l TODO | xargs sed -n '1,5p'
```

## Regular expressions

You do not need to master regex immediately, but you should know the basics.

Examples:

```console
$ rg '^def ' src
$ rg 'test_.*_error' tests
$ rg '[0-9]{4}' docs
```

Useful pieces:

- `^` start of line
- `$` end of line
- `.` any character
- `*` zero or more
- `+` one or more
- `?` zero or one
- `[abc]` character class
- `[0-9]` range
- `{n}` exact count

## Archives and compression

Create a tar archive:

```console
$ tar cf archive.tar docs/
```

Create a gzipped tar archive:

```console
$ tar czf archive.tar.gz docs/
```

Extract:

```console
$ tar xzf archive.tar.gz
```

Zip:

```console
$ zip -r docs.zip docs/
$ unzip docs.zip
```

## Networking basics

Check a URL:

```console
$ curl http://localhost:8000/health
```

Fetch headers only:

```console
$ curl -I https://ospsd-team-2.fly.dev
```

Download a file:

```console
$ curl -O https://example.com/file.txt
```

Inspect DNS or host resolution:

```console
$ ping github.com
$ nslookup github.com
```

This repository's docs include practical `curl` workflows for the service (`docs/source/curl-tutorial.md:12-104`).

## Shell startup files

Your shell can read startup files such as:

- `~/.bashrc`
- `~/.bash_profile`
- `~/.zshrc`

That is where aliases, functions, and exported environment variables often live.

## Aliases and functions

Alias example:

```console
$ alias ll='ls -la'
```

Function example:

```console
mkcd () {
  mkdir -p "$1"
  cd "$1"
}
```

Put them in your shell startup file if you want them to persist.

## Writing shell scripts

Simple shell script:

```sh
#!/usr/bin/env bash
set -euo pipefail

uv run pytest -q
uv run ruff check .
uv run mypy --strict .
```

Make it executable:

```console
$ chmod +x verify.sh
```

Run it:

```console
$ ./verify.sh
```

### Common shell script pieces

Variables:

```sh
name="world"
echo "hello $name"
```

Conditionals:

```sh
if [ -f .env ]; then
  echo "found .env"
fi
```

Loops:

```sh
for file in docs/*.md; do
  echo "$file"
done
```

Exit on failure:

```sh
set -e
```

Safe script mode:

```sh
set -euo pipefail
```

## Real command loop for this repo

```console
$ cd ~/Work/ospsd-team-2
$ uv sync --all-packages
$ uv run pytest -q
$ uv run ruff check .
$ uv run mypy --strict .
$ uv run uvicorn aws_client_service.main:app --reload
$ uv run sphinx-autobuild docs/source docs/build/html
```

## Further reading

- *The Linux Command Line, 3rd Edition*: <https://nostarch.com/linux-command-line-3e>
