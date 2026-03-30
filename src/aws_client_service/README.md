# aws-client-service

FastAPI service that exposes the AWS S3 cloud storage client over HTTP.

## Role

This package is the deployment unit. It wraps `aws-client-impl` and exposes its functionality as HTTP endpoints.

## API

| Method | Path | Query/Body params | Success | Errors |
|--------|------|-------------------|---------|--------|
| `GET` | `/health` | — | `200 {"status": "ok"}` | — |
| `GET` | `/` | — | `200 {"message": "Hello World"}` | — |
| `POST` | `/files/{container}/{object_name}` | `file` (multipart) | `200 {"ok": true}` | `400` invalid key, `422` missing params, `502` storage error |
| `GET` | `/download` | `bucket_name`, `object_name` | `200` file download | `404` not found, `422` missing params, `502` storage error |
| `DELETE` | `/files/{container}/{object_name}` | `container`, `object_name` (path) | `200 {"ok": true}` | `404` not found, `502` storage error |

### `POST /files/{container}/{object_name}`

Uploads a file to an S3 bucket via multipart form data.

**Path parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | `string` | yes | S3 bucket name |
| `object_name` | `string` | yes | S3 object key (e.g. `reports/data.csv`) |

**Form data:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | `binary` | yes | File to upload (multipart form data) |

**Responses:**

- `200` — `{"ok": true}` — file uploaded successfully
- `400` — invalid key (empty or starts with a leading slash)
- `422` — missing or invalid parameters
- `502` — unexpected storage exception (details logged server-side)

Example:
```bash
curl -X POST "http://localhost:8000/files/my-bucket/reports/data.csv" \
  -F "file=@/path/to/local/data.csv"
```

### `GET /download`

Downloads an object from S3 and streams it back as a file response.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `bucket_name` | `string` | yes | S3 bucket name |
| `object_name` | `string` | yes | S3 object key (e.g. `reports/data.csv`) |

**Responses:**

- `200` — file content with `Content-Disposition: attachment; filename=<basename>`
- `404` — object not found or `download_file` returned `False`
- `422` — missing or invalid query parameters
- `502` — unexpected storage exception (details logged server-side)

The response filename is set to the basename of `object_name` (e.g. `data.csv` for `reports/data.csv`). The temp file created during the download is deleted automatically after the response is sent.

### `DELETE /files/{container}/{object_name}`

Deletes an object from an S3 bucket and returns a JSON confirmation.

**Path parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | `string` | yes | S3 bucket name |
| `object_name` | `string` | yes | S3 object key (e.g. `reports/data.csv`) |

**Responses:**

- `200` — `{"ok": true}` — object deleted successfully
- `404` — object not found or `delete_file` returned `False`
- `502` — unexpected storage exception (details logged server-side)

Example:

```bash
curl -X DELETE "http://localhost:8000/files/my-bucket/reports/data.csv"
```

## Dependencies

- `fastapi` — web framework
- `uvicorn` — ASGI server
- `python-multipart` — multipart form data parsing for file uploads
- `cloud-storage-client-api` — abstract storage interface
- `aws-client-impl` — S3 implementation (registered via dependency injection on import)
- `structlog` — structured logging

## Usage

```bash
uv run uvicorn aws_client_service.main:app --reload
```

Upload a file:
```bash
curl -X POST "http://localhost:8000/files/my-bucket/reports/data.csv" \
  -F "file=@/path/to/local/data.csv"
```

Download a file:
```bash
curl "http://localhost:8000/download?bucket_name=my-bucket&object_name=reports/data.csv" \
  --output data.csv
```

Delete an object:
```bash
curl -X DELETE "http://localhost:8000/files/my-bucket/reports/data.csv"
```