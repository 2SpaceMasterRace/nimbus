# aws-client-service

FastAPI deployment unit that exposes the S3-backed storage client over HTTP.

## Role

This package wraps `aws-client-impl`, handles authentication, translates domain
exceptions into HTTP responses, and publishes the OpenAPI schema used to
generate the service client.

## Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/` | Root endpoint |
| `GET` | `/auth/login` | Start GitHub OAuth flow |
| `GET` | `/auth/callback` | Complete GitHub OAuth flow |
| `POST` | `/files/{container}/{object_name}` | Upload an object |
| `GET` | `/files` | List files in a container |
| `GET` | `/download` | Download an object |
| `DELETE` | `/files/{container}/{object_name}` | Delete an object |

Protected file routes accept either:

- a GitHub OAuth session cookie, or
- an API key supplied via `X-API-Key` / `Authorization: Bearer ...`

## Example Usage

```bash
export API_KEY="replace-me"
uv run uvicorn aws_client_service.main:app --reload

curl -X POST "http://localhost:8000/files/my-bucket/reports/data.csv" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@/path/to/local/data.csv"

curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/files?container=my-bucket&prefix=reports/"

curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/download?container=my-bucket&object_name=reports/data.csv" \
  --output data.csv
```

## Dependencies

- `fastapi`
- `uvicorn`
- `python-multipart`
- `cloud-storage-client-api`
- `aws-client-impl`
- `structlog`
