# Cloud Storage HTTP API

The storage HTTP API is resource-oriented and uses standard HTTP verbs and
status codes. Protected routes accept either a GitHub OAuth session or an API
key.

**Base URL**

```text
http://localhost:8000
```

**Auth header**

```text
X-API-Key: <API_KEY>
```

or:

```text
Authorization: Bearer <API_KEY>
```

## Object resource

The object resource is identified by:

```text
/files/{container}/{object_name:path}
```

Because `object_name` is a path parameter, slashes are valid:

```text
/files/my-bucket/reports/2026/april.csv
```

## Upload an object

```text
POST /files/{container}/{object_name:path}
Content-Type: multipart/form-data
X-API-Key: <API_KEY>
```

```shell
curl -X POST "http://localhost:8000/files/my-bucket/reports/april.csv" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@reports/april.csv"
```

Successful response:

```json
{
  "object_name": "reports/april.csv",
  "version_id": null,
  "data_type": "text/csv",
  "integrity": "\"abc123\"",
  "encryption": null,
  "storage_tier": null,
  "size_bytes": 128,
  "updated_at": "2026-04-29T14:30:00Z",
  "metadata": {}
}
```

## List objects

```text
GET /files?container={container}&prefix={prefix}
X-API-Key: <API_KEY>
```

```shell
curl "http://localhost:8000/files?container=my-bucket&prefix=reports/" \
  -H "X-API-Key: $API_KEY"
```

Successful response:

```json
[
  {
    "object_name": "reports/april.csv",
    "size_bytes": 128,
    "updated_at": "2026-04-29T14:30:00Z"
  }
]
```

## Download an object

```text
GET /download?container={container}&object_name={object_name}
X-API-Key: <API_KEY>
```

```shell
curl "http://localhost:8000/download?container=my-bucket&object_name=reports/april.csv" \
  -H "X-API-Key: $API_KEY" \
  --output april.csv
```

The service downloads the object to a temporary local file, streams it as a
`FileResponse`, and removes the temp file after the response is sent.

## Get object metadata

```text
GET /files/{container}/{object_name:path}/info
X-API-Key: <API_KEY>
```

```shell
curl "http://localhost:8000/files/my-bucket/reports/april.csv/info" \
  -H "X-API-Key: $API_KEY"
```

Use this route when you need metadata but not bytes.

## Delete an object

```text
DELETE /files/{container}/{object_name:path}
X-API-Key: <API_KEY>
```

```shell
curl -X DELETE "http://localhost:8000/files/my-bucket/reports/april.csv" \
  -H "X-API-Key: $API_KEY"
```

Successful response:

```json
{
  "deleted": true,
  "version_id": null,
  "request_charged": null
}
```

## Status codes

| Status | Meaning |
|---|---|
| `200` | Operation succeeded. |
| `400` | Container, object name, or file object failed domain validation. |
| `401` | Missing or invalid auth. |
| `404` | Container or object was not found. |
| `422` | Request failed FastAPI/Pydantic validation. |
| `502` | Storage provider failed unexpectedly. |

## Error response shape

FastAPI returns errors as JSON with a `detail` field. Domain errors use a string
detail:

```json
{
  "detail": "Object 'reports/missing.csv' was not found in container 'my-bucket'"
}
```

Validation errors use FastAPI's structured validation shape:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["query", "container"],
      "msg": "Field required",
      "input": null
    }
  ]
}
```

Treat `detail` as diagnostic text, not as a stable machine enum. Use HTTP status
and route context for programmatic handling. Python callers that need typed
errors should use {doc}`service-adapter`.

## Retry guidance

The storage API does not currently expose an HTTP idempotency key. Safe retry
guidance:

| Operation | Retry guidance |
|---|---|
| `GET /files` | Safe to retry. |
| `GET /download` | Safe to retry. |
| `GET /files/.../info` | Safe to retry. |
| `POST /files/...` | Retry only if the caller can tolerate replacing the same key with the same bytes. |
| `DELETE /files/...` | Retry only after checking whether the object still exists; missing objects return `404`. |

For wrapper chat turns, idempotency exists at `/ai/chat/turn`, not on storage
routes. See {doc}`../nimbus/bridge-contract`.

## Complete curl workflow

```shell
export BASE_URL="http://localhost:8000"
export API_KEY="dev-storage-api-key"
export CONTAINER="my-bucket"

printf 'hello from storage docs\n' > /tmp/nimbus-storage-demo.txt

curl -X POST "$BASE_URL/files/$CONTAINER/docs/demo.txt" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@/tmp/nimbus-storage-demo.txt"

curl "$BASE_URL/files?container=$CONTAINER&prefix=docs/" \
  -H "X-API-Key: $API_KEY"

curl "$BASE_URL/files/$CONTAINER/docs/demo.txt/info" \
  -H "X-API-Key: $API_KEY"

curl "$BASE_URL/download?container=$CONTAINER&object_name=docs/demo.txt" \
  -H "X-API-Key: $API_KEY" \
  --output /tmp/nimbus-storage-downloaded.txt

curl -X DELETE "$BASE_URL/files/$CONTAINER/docs/demo.txt" \
  -H "X-API-Key: $API_KEY"
```
