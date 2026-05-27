FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

COPY . /app

RUN uv sync --all-packages --all-groups --frozen --python /usr/local/bin/python3.12 \
    && uv run sphinx-build docs/source docs/build/html \
    && test -f docs/build/html/index.html

EXPOSE 8080

CMD ["sh", "-c", "uv run uvicorn aws_client_service.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
