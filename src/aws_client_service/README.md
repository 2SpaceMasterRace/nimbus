# aws-client-service

FastAPI service that exposes the AWS S3 cloud storage client over HTTP

## Role

This package is the deployment unit. It wraps `aws-client-impl` and exposes its functionality as HTTP endpoints.

## API

- `GET /health` — health check, returns `{"status": "ok"}`
- `GET /` — root endpoint

## Dependencies

- `fastapi` — web framework
- `uvicorn` — ASGI server

## Usage
```bash
uv run uvicorn aws_client_service.main:app --reload
```