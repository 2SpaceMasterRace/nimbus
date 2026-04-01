# Docker

Docker is not just a command you run to make something start. It is a way of packaging software, its runtime environment, and its launch instructions into something portable.

The official Docker docs describe Docker as an open platform for developing, shipping, and running applications, and describe containers as lightweight isolated environments containing everything needed to run an app.

That definition becomes much more useful once you can answer two questions clearly:

1. what actually happens when you build and run a container?
2. how do teams use containers in real production?

This section answers both.

## The core vocabulary

You need a few words straight before the rest makes sense.

- **image**: a build artifact or template
- **container**: a running instance of an image
- **Dockerfile**: the file that describes how to build the image
- **registry**: a place that stores images
- **layer**: one cached build step inside an image
- **build context**: the files Docker can see during build
- **volume**: persistent storage mounted into a container

## What Docker is doing conceptually

Think of a Docker image as a frozen recipe plus a frozen filesystem.

It says things like:

- start from this base operating system image,
- install these packages,
- copy these files,
- run these setup commands,
- expose this port,
- launch this process when the container starts.

When you run a container, Docker creates an isolated runtime instance from that image.

That means:

- the process runs with its own filesystem view,
- it gets its own environment variables,
- it can have its own networking setup,
- and it behaves much more predictably than "whatever happens to be installed on this laptop."

## How `docker build` works

When you run:

```console
$ docker build -t cloud-storage-service .
```

Docker does roughly this:

1. sends the build context from the current directory,
2. reads the `Dockerfile`,
3. executes each instruction in order,
4. creates a new image layer for relevant steps,
5. caches layers so repeated builds can be faster,
6. tags the final image as `cloud-storage-service`.

The current repository's `Dockerfile` is short enough to study directly (`Dockerfile:1-25`):

- it starts from `python:3.12-slim` (`Dockerfile:1`)
- sets Python and uv-related environment variables (`Dockerfile:3-6`)
- sets `/app` as the working directory (`Dockerfile:8`)
- installs `curl` and CA certificates (`Dockerfile:10-12`)
- installs `uv` (`Dockerfile:14-15`)
- copies the repository into the image (`Dockerfile:17`)
- syncs the workspace dependencies and builds the docs (`Dockerfile:19-21`)
- exposes port `8080` (`Dockerfile:23`)
- starts the FastAPI app with Uvicorn (`Dockerfile:25`)

That is a complete deployable artifact.

## Read the Dockerfile line by line

### Base image

```dockerfile
FROM python:3.12-slim
```

This says: start from the official slim Python runtime image.

That gives the container a base Linux filesystem plus Python.

### Environment variables at build and runtime

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy
```

These environment variables tune Python and `uv` behavior inside the image.

### Working directory

```dockerfile
WORKDIR /app
```

From this point on, most commands run relative to `/app`.

### OS package installation

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
```

This installs system-level packages the image needs.

### Install `uv`

```dockerfile
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
```

Now `uv` is available inside the image.

### Copy repository files

```dockerfile
COPY . /app
```

This copies the build context into the image.

This is why `.dockerignore` matters: it controls what gets sent into the build context in the first place.

### Install project dependencies and verify docs build

```dockerfile
RUN uv sync --all-packages --all-groups --frozen \
    && uv run sphinx-build docs/source docs/build/html \
    && test -f docs/build/html/index.html
```

This is an important production-quality pattern.

The image build is not only installing dependencies. It is also verifying that the docs build successfully before the image is considered complete.

### Expose port and run command

```dockerfile
EXPOSE 8080
CMD ["sh", "-c", "uv run uvicorn aws_client_service.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
```

`EXPOSE` documents the intended port.

`CMD` defines the default startup process for the container.

The app binds to `0.0.0.0` because a containerized service must listen on the container network interface, not only on localhost.

## How `docker run` works

When you run a container, Docker starts a new isolated process environment from the image.

In this repository, the README shows a real local container test (`README.md:287-318`):

```console
$ docker run --rm \
    -p 8080:8080 \
    -e SESSION_SECRET_KEY="replace-me" \
    -e API_KEY="replace-me" \
    -e AWS_ACCESS_KEY_ID="replace-me" \
    -e AWS_SECRET_ACCESS_KEY="replace-me" \
    -e AWS_REGION="us-east-1" \
    -e AWS_BUCKET_NAME="replace-me" \
    -e CLOUD_STORAGE_SERVICE_BASE_URL="http://localhost:8080" \
    -e GITHUB_CLIENT_ID="replace-me" \
    -e GITHUB_CLIENT_SECRET="replace-me" \
    -e GITHUB_AUTH_URI="https://github.com/login/oauth/authorize" \
    -e GITHUB_TOKEN_URI="https://github.com/login/oauth/access_token" \
    -e GITHUB_LOCAL_REDIRECT_URI="http://localhost:8080/auth/callback" \
    cloud-storage-service
```

Here is what the main flags mean.

### `--rm`

Delete the container after it exits.

Good for local test runs.

### `-p 8080:8080`

Map host port `8080` to container port `8080`.

Format:

```text
host_port:container_port
```

### `-e NAME=value`

Inject runtime environment variables.

This is how secrets and config are passed into the app at runtime.

### `cloud-storage-service`

This is the image name to run.

## The lifecycle of a container

For day-to-day work, you should know these commands.

Build:

```console
$ docker build -t cloud-storage-service .
```

Run:

```console
$ docker run --rm -p 8080:8080 cloud-storage-service
```

See running containers:

```console
$ docker ps
```

See all containers, including stopped ones:

```console
$ docker ps -a
```

See logs:

```console
$ docker logs <container>
```

Open a shell inside a running container:

```console
$ docker exec -it <container> /bin/sh
```

Stop a container:

```console
$ docker stop <container>
```

Remove an image:

```console
$ docker rmi cloud-storage-service
```

## Volumes and persistence

Containers are usually treated as disposable.

That means if you write data inside a container's filesystem and then destroy the container, that data may be lost.

If you need persistence, use:

- an external service,
- a database,
- object storage,
- or a Docker volume.

This repository is a good example of an app that should mostly be stateless in production.

It stores important long-lived data externally in S3 and receives runtime config via environment variables.

That is a very production-friendly shape.

## Networking

Containers run in their own network environment.

That is why local container usage often needs explicit port mapping.

Inside the container, the app listens on `8080`.

Outside the container, you reach it through whatever host port you mapped.

Example from this repo:

```console
$ curl http://localhost:8080/health
```

That corresponds to the host side of `-p 8080:8080`.

## Images are immutable, containers are disposable

This is one of the most important production concepts.

You do not usually SSH into a production container and hand-edit files to "fix it."

Instead, you:

1. change the code,
2. build a new image,
3. deploy the new image,
4. replace the old running containers.

That is the healthy production model.

## Registries

A registry stores images so they can be deployed elsewhere.

Examples of registries:

- Docker Hub
- GitHub Container Registry
- Amazon ECR
- Google Artifact Registry

Typical production flow:

1. CI builds an image
2. CI tags the image
3. CI pushes the image to a registry
4. deploy platform pulls that image
5. deploy platform starts new containers from it

## How Docker is used in real production

In real production, Docker is usually not the final destination. It is the packaging unit.

A team builds a container image because they want:

- the same app everywhere,
- a repeatable build artifact,
- predictable runtime behavior,
- easy deployment to a host or orchestrator.

Then that image runs on:

- a single VM,
- Fly.io,
- ECS,
- Kubernetes,
- Nomad,
- Cloud Run,
- or some other container-aware platform.

### The usual production pattern

The normal production pattern looks like this:

1. build once
2. tag the image
3. push it to a registry or let the platform build it
4. inject environment-specific secrets at runtime
5. run health checks
6. replace old instances with new ones

This is very different from "run a script directly on one laptop."

### Secrets in production

Do not bake secrets into the image.

Good pattern:

- image contains code and runtime dependencies
- secrets are injected by the deploy platform at runtime

This repository's README already follows that pattern for Fly.io secrets (`README.md:342-357`).

### Health checks in production

A production platform needs a way to ask: is the app healthy?

This repo has an explicit health route and Fly.io health checks.

From `fly.toml:18-26`, the deployed service is checked with:

- `GET /health`
- every `30s`
- with a `5s` timeout

That is exactly what real production systems do. They need a machine-readable health endpoint.

### Stateless app design

Production containers are easiest to operate when they are stateless.

This app is close to that pattern:

- code lives in the image
- runtime configuration comes from environment variables
- durable storage lives outside the container in S3

That is much easier to scale and redeploy than if the app stored its critical data only inside the local container filesystem.

### Horizontal scaling

Once an app is packaged as a container and built to be mostly stateless, it becomes much easier to run multiple copies.

That is one reason container-based production is so popular.

## How this repository uses Docker in practice

This repository uses Docker as the deployable packaging unit.

You can see that in three places.

### The Dockerfile

The image build and startup behavior lives in `Dockerfile:1-25`.

### The Fly.io config

`fly.toml` points Fly at the Dockerfile and configures the runtime (`fly.toml:1-26`).

Important lines:

- `dockerfile = "Dockerfile"` (`fly.toml:4-5`)
- `PORT = "8080"` (`fly.toml:7-8`)
- `internal_port = 8080` (`fly.toml:10-16`)
- health check on `/health` (`fly.toml:18-26`)

### The local container check

The README includes a full local test run of the image (`README.md:287-318`).

That is valuable because it lets you verify the deployable unit before you deploy it.

## A realistic production story for this repo

Here is what a real production loop looks like for a service like this.

1. developer changes code
2. tests and type checks run
3. image is built
4. image is deployed to Fly
5. Fly injects runtime secrets
6. Fly starts the new container
7. Fly checks `/health`
8. if healthy, traffic keeps flowing to the new version

That is a very standard containerized production pattern.

## Local workflow with Docker for this repo

Build:

```console
$ docker build -t cloud-storage-service .
```

Run:

```console
$ docker run --rm \
  -p 8080:8080 \
  -e SESSION_SECRET_KEY="replace-me" \
  -e API_KEY="replace-me" \
  -e AWS_ACCESS_KEY_ID="replace-me" \
  -e AWS_SECRET_ACCESS_KEY="replace-me" \
  -e AWS_REGION="us-east-1" \
  -e AWS_BUCKET_NAME="replace-me" \
  -e CLOUD_STORAGE_SERVICE_BASE_URL="http://localhost:8080" \
  -e GITHUB_CLIENT_ID="replace-me" \
  -e GITHUB_CLIENT_SECRET="replace-me" \
  -e GITHUB_AUTH_URI="https://github.com/login/oauth/authorize" \
  -e GITHUB_TOKEN_URI="https://github.com/login/oauth/access_token" \
  -e GITHUB_LOCAL_REDIRECT_URI="http://localhost:8080/auth/callback" \
  cloud-storage-service
```

Verify:

```console
$ curl http://localhost:8080/health
```

## Common mistakes with Docker

This section does not need philosophy, but it does need practical warnings.

Common mistakes:

- baking secrets into the image
- confusing an image with a container
- treating container filesystem state as permanent
- binding only to localhost inside the container
- forgetting port mapping
- using Docker everywhere when `uv` is simpler for the local inner loop
- manually patching running production containers instead of rebuilding images

## Further reading

- Docker overview: <https://docs.docker.com/get-started/docker-overview/>
- `Dockerfile:1-25`
- `fly.toml:1-26`
- `README.md:287-366`
