# Nix and Reproducible Environments

Nix is a package manager and build system that aims to make environments reproducible.

In practical terms, that means you can describe your tooling once and then reuse that description across:

- local development,
- CI,
- Docker image builds,
- and sometimes production.

The reason people get excited about Nix is not because the syntax is pretty. The reason is that it can become a single source of truth for how software is built and what tools it needs.

## What problem Nix is solving

In many projects, the build and runtime story is split across several separate descriptions.

Typical non-Nix setup:

- a README explaining local setup,
- CI YAML explaining CI setup,
- a Dockerfile explaining production image setup,
- maybe shell scripts explaining another variant of the same setup.

That is the pain Mitchell Hashimoto calls out in "Using Nix with Dockerfiles." You update local setup and Docker, then forget CI. Or local and CI work, but production breaks. Or your machine works, but a teammate's machine does not.

The Nix idea is: describe the environment once, and reuse it.

## Install Nix

The Nix reference manual recommends the multi-user install on Linux and macOS.

Install with the daemon mode:

```console
$ curl -L https://nixos.org/nix/install | sh -s -- --daemon
```

Typical output looks like:

```text
downloading Nix installer...
performing a multi-user installation of Nix...
copying Nix to /nix/store...
installation finished
```

After installing, open a new shell and check the version:

```console
$ nix --version
```

Typical output:

```text
nix (Nix) 2.x
```

## Ad hoc shell environments

One of the easiest ways to start using Nix is to create temporary tool environments.

The official `nix.dev` tutorial shows `nix-shell -p ...` for this.

Example:

```console
$ nix-shell -p git neovim nodejs
```

Typical first-time output:

```text
these paths will be fetched...
[nix-shell:~/project]$
```

Then inside the shell:

```console
[nix-shell:~/project]$ git --version
[nix-shell:~/project]$ nvim --version | head -n 1
[nix-shell:~/project]$ node --version
```

Typical output:

```text
git version 2.x
NVIM v0.x
vXX.Y.Z
```

This is very useful when you want to try tools without installing them globally.

## Flakes

Flakes are an experimental but widely used Nix feature for pinning dependencies and defining outputs in a standard structure.

The important beginner ideas are:

- a flake is a directory with a `flake.nix`
- flakes typically have a `flake.lock`
- inputs are pinned
- outputs can include packages, dev shells, apps, and more

The NixOS Wiki explains flakes as a way to write Nix expressions with dependencies pinned in a lock file, improving reproducibility.

## A real example in this repo

I added a repo-local example flake here:

- `docs/examples/nix/flake.nix:1-35`

This example gives you:

- a development shell with `git`, `gh`, `docker`, `tmux`, `zsh`, `ripgrep`, `fd`, `jq`, `python312`, and `uv`
- a simple package called `show-tool-versions`

### Enter the dev shell

From the repo root:

```console
$ nix develop ./docs/examples/nix
```

Typical output:

```text
Entered the example Nix dev shell
Try: uv --version && python --version && git --version
```

Then try:

```console
$ uv --version
$ python --version
$ git --version
```

Typical output:

```text
uv 0.x.x
Python 3.12.x
git version 2.x
```

### Build the example package

```console
$ nix build ./docs/examples/nix
```

Typical output:

```text
building '/nix/store/...-show-tool-versions.drv'...
```

Now run the result:

```console
$ ./result/bin/show-tool-versions
```

Typical output:

```text
git: git version 2.x
gh: gh version X.Y.Z
python: Python 3.12.x
uv: uv 0.x.x
```

## Why this matters for projects

In a normal project, a Nix flake can define:

- the developer shell,
- helper apps,
- build outputs,
- and in some setups the deployable artifact.

This lets a new contributor get the exact same toolchain more easily.

That matters for teams because it reduces:

- "works on my machine" problems,
- tool version drift,
- repeated setup instructions,
- differences between local and CI.

## Nix with Dockerfiles

Mitchell Hashimoto's article makes one especially practical point: Dockerfiles are easy, but they become much better when they reuse the same Nix definition as local dev and CI.

That gives you one shared build truth instead of three or four slightly different setup scripts.

The pattern is:

1. write Nix code that describes the environment and package
2. use a Dockerfile with the official Nix image to run `nix build`
3. copy the build result and closure into a minimal final image

I added a repo-local example Dockerfile here:

- `docs/examples/nix/Dockerfile:1-15`

## Read the Nix Dockerfile example

### Builder stage

```dockerfile
FROM nixos/nix:latest AS builder
```

This starts from the official Nix image.

### Copy the source

```dockerfile
COPY . /tmp/build
WORKDIR /tmp/build/docs/examples/nix
```

Now the builder can see the example flake.

### Build with Nix

```dockerfile
RUN nix \
    --extra-experimental-features "nix-command flakes" \
    build
```

This runs the flake build inside the container.

Typical output during build will include store paths and derivation builds.

### Copy the Nix store closure

```dockerfile
RUN mkdir /tmp/nix-store-closure
RUN cp -R $(nix-store -qR result/) /tmp/nix-store-closure
```

This is one of the key ideas from Mitchell's article.

The closure is the full set of Nix store paths your built result needs to run.

### Final image from scratch

```dockerfile
FROM scratch

WORKDIR /app
COPY --from=builder /tmp/nix-store-closure /nix/store
COPY --from=builder /tmp/build/docs/examples/nix/result /app

CMD ["/app/bin/show-tool-versions"]
```

The final image does not need the full Nix toolchain anymore. It only needs the closure and the built result.

That is the production-friendly part of this approach.

## Build the example Docker image

From the repo root:

```console
$ docker build -f docs/examples/nix/Dockerfile -t nix-tool-demo .
```

Typical output:

```text
[builder 1/6] FROM nixos/nix:latest
[builder 4/6] RUN nix --extra-experimental-features "nix-command flakes" build
...
Successfully tagged nix-tool-demo:latest
```

Run it:

```console
$ docker run --rm nix-tool-demo
```

Typical output:

```text
git: git version 2.x
gh: gh version X.Y.Z
python: Python 3.12.x
uv: uv 0.x.x
```

## How Nix fits with this repository

This repository already uses:

- `uv` for Python dependency and workspace management (`pyproject.toml`, `README.md:103-111`)
- Docker for packaging and deployment (`Dockerfile:1-25`, `README.md:287-366`)
- CircleCI for CI (`.circleci/config.yml`)

Nix does not have to replace `uv`.

A practical way to use Nix here would be:

- let `uv` continue managing Python package installation
- let Nix manage the outer toolchain and OS-level reproducibility

That means Nix can provide a stable shell containing:

- Python
- `uv`
- Git
- `gh`
- Docker
- shell tools like `rg`, `fd`, `jq`

while `uv sync --all-packages` still handles the Python workspace itself.

That is often the cleanest hybrid model.

## Nix for local development

Good local uses:

- standardize tool versions across contributors
- provide a one-command dev shell
- avoid global installs
- make onboarding easier
- reduce shell setup differences between macOS and Linux

Typical workflow:

```console
$ nix develop ./docs/examples/nix
$ uv sync --all-packages
$ uv run pytest -q
```

## Nix for CI

Nix can also be the thing that prepares the CI environment.

That means CI can use the same pinned tool definitions as development.

In a real project, that often reduces drift between:

- developer machines
- CI runners
- image builds

This is exactly the class of problem Mitchell's Dockerfile article is trying to eliminate.

## Nix for production

Nix is not required for production, but it can be very useful there.

The most practical use in many teams is:

- Nix builds the package or environment
- Docker packages the runtime artifact
- the deploy platform runs the container

That gives you:

- reproducible builds
- fewer environment mismatches
- smaller runtime images when you copy only the closure you need

## `nix-shell` versus `nix develop`

Older workflows often use `nix-shell`.

Modern flake workflows often use `nix develop`.

In practice:

- use `nix-shell -p ...` for quick ad hoc environments
- use `nix develop` for flake-defined project environments

## `direnv` and automatic shells

Once you use Nix regularly, `direnv` or `nix-direnv` becomes very useful.

It lets you automatically enter the right environment when you `cd` into a project.

That is a natural next step after basic flakes.

## Common mistakes with Nix

Common mistakes in project usage:

- expecting Nix to replace every other tool immediately
- mixing unpinned and pinned environments without noticing
- forgetting that flakes in Git repos only see files in the working tree or staged tree, depending on the workflow
- putting secrets into flake files
- assuming Nix makes understanding your build optional

The Nix wiki explicitly warns against putting secrets in flake files because flake contents are copied into the store.

## Further reading

- Mitchell Hashimoto, "Using Nix with Dockerfiles": <https://mitchellh.com/writing/nix-with-dockerfiles>
- Nix installation manual: <https://nix.dev/manual/nix/2.32/installation>
- Nix flakes overview: <https://nixos.wiki/wiki/Flakes>
- nix.dev ad hoc shell environments: <https://nix.dev/tutorials/first-steps/ad-hoc-shell-environments>
- `docs/examples/nix/flake.nix:1-35`
- `docs/examples/nix/Dockerfile:1-15`
