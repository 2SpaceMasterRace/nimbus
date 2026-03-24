# API Reference

## HTTP Service (`aws_client_service`)

The FastAPI service exposes the cloud storage client over HTTP.

**Base URL:** `http://localhost:8000` (default when running locally)

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

### `GET /download`

Download an S3 object and stream it back as a file.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `bucket_name` | string | yes | S3 bucket name |
| `object_name` | string | yes | S3 object key (e.g. `reports/data.csv`) |

**Responses:**

| Status | Meaning |
|--------|---------|
| `200` | File content; `Content-Disposition` header set to the object's basename |
| `404` | Object not found or storage client returned failure |
| `422` | Missing or invalid query parameters |
| `502` | Unexpected storage exception (details logged server-side) |

**Example:**

```bash
curl "http://localhost:8000/download?bucket_name=my-bucket&object_name=reports/data.csv" \
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
| `404` | Object not found or storage client returned failure |
| `502` | Unexpected storage exception (details logged server-side) |

**Example:**

```bash
curl -X DELETE "http://localhost:8000/files/my-bucket/reports/data.csv"
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
