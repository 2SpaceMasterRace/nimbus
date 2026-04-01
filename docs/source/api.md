# API Reference

## HTTP Service (`aws_client_service`)

The FastAPI service exposes the cloud storage client over HTTP.

**Base URL:** `http://localhost:8000` (default when running locally)

Protected file endpoints accept either:

- a GitHub OAuth session cookie created through `/auth/login` and `/auth/callback`, or
- an API key supplied via `X-API-Key` or `Authorization: Bearer <api-key>`

### `GET /health`

Health check.

**Response `200`:**
```json
{"status": "ok"}
```

---

### `GET /`

Root endpoint.

**Response `200`:**
```json
{"message": "Hello World"}
```

---

### `POST /files/{container}/{object_name}`

Upload a file to an S3 bucket.

**Path parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | string | yes | S3 bucket name |
| `object_name` | string | yes | S3 object key (e.g. `reports/data.csv`) |

**Form data:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | binary | yes | File to upload (multipart form data) |

**Responses:**

| Status | Meaning |
|--------|---------|
| `200` | `{"ok": true}` — file uploaded successfully |
| `400` | Invalid key or bucket does not match the configured service bucket |
| `422` | Missing or invalid parameters |
| `502` | Unexpected storage exception (details logged server-side) |

**Example:**

```bash
curl -X POST "http://localhost:8000/files/my-bucket/reports/data.csv" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@/path/to/local/data.csv"
```

---

### `GET /files`

List files in a container.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | string | yes | S3 bucket name |
| `prefix` | string | no | Optional key prefix |

**Responses:**

| Status | Meaning |
|--------|---------|
| `200` | `{"files": ["..."]}` |
| `400` | Invalid container name |
| `422` | Missing or invalid parameters |
| `502` | Unexpected storage exception |

---

### `GET /download`

Download an S3 object and stream it back as a file.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | string | yes | S3 bucket name |
| `object_name` | string | yes | S3 object key (e.g. `reports/data.csv`) |

**Responses:**

| Status | Meaning |
|--------|---------|
| `200` | File content; `Content-Disposition` header set to the object's basename |
| `400` | Invalid container or object name |
| `404` | Object not found or storage client returned failure |
| `422` | Missing or invalid query parameters |
| `502` | Unexpected storage exception (details logged server-side) |

**Example:**

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/download?container=my-bucket&object_name=reports/data.csv" \
  --output data.csv
```

The temp file created during the download is deleted automatically after the response finishes sending.

---

### `DELETE /files/{container}/{object_name}`

Delete an object from an S3 bucket.

**Path parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | string | yes | S3 bucket name |
| `object_name` | string | yes | S3 object key (e.g. `reports/data.csv`) |

**Responses:**

| Status | Meaning |
|--------|---------|
| `200` | `{"ok": true}` — object deleted successfully |
| `400` | Invalid container or object name |
| `404` | Object not found or storage client returned failure |
| `502` | Unexpected storage exception (details logged server-side) |

**Example:**

```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/files/my-bucket/reports/data.csv"
```

---

## Python Client (`cloud_storage_client_api`)

```{eval-rst}
.. automodule:: cloud_storage_client_api.client
   :members:
   :undoc-members:
   :show-inheritance:
```

```{eval-rst}
.. automodule:: cloud_storage_client_api.factory
   :members:
   :undoc-members:
   :show-inheritance:
```

## S3 Implementation (`aws_client_impl`)

```{eval-rst}
.. automodule:: aws_client_impl.s3_client
   :members:
   :undoc-members:
   :show-inheritance:
```
