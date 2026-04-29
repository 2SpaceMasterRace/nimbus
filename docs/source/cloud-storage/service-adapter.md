# Service Adapter

`aws_client_adapter` is what lets remote storage calls feel like local
`CloudStorageClient` calls.

## Why it exists

The generated OpenAPI client mirrors HTTP. It has endpoint-shaped function names,
HTTP response objects, generated models, and transport details. That is useful
for low-level clients but awkward for application code.

The adapter restores the provider-neutral contract:

```python
from aws_client_adapter import get_client_impl

client = get_client_impl()
client.list_files("my-bucket", "reports/")
```

Only the factory import changes compared with the local S3 client.

## Configuration

```shell
export CLOUD_STORAGE_SERVICE_BASE_URL="http://localhost:8000"
export API_KEY="dev-storage-api-key"
```

If `API_KEY` is set, the adapter uses the generated `AuthenticatedClient`. If it
is missing, it uses the unauthenticated generated `Client`; protected storage
routes will then return `401`.

## Mapping behavior

| Contract method | HTTP route used |
|---|---|
| `upload_file` | Opens the local file, then delegates to `upload_obj`. |
| `upload_obj` | `POST /files/{container}/{object_name}` |
| `download_file` | `GET /download`, then writes bytes locally, then calls metadata route |
| `list_files` | `GET /files` |
| `delete_file` | `DELETE /files/{container}/{object_name}` |
| `get_file_info` | `GET /files/{container}/{object_name}/info` |

## Error translation

| HTTP/transport condition | Domain exception |
|---|---|
| httpx transport error | `StorageBackendError` |
| `400` invalid container | `InvalidContainerError` |
| `400` invalid object name | `InvalidObjectNameError` |
| `401` | `AuthenticationError` |
| `404` | `ObjectNotFoundError` or `ContainerNotFoundError`, depending on route/detail |
| `422` upload/form validation | `InvalidFileObjectError` |
| `5xx` | `StorageBackendError` |

This is why callers can share error handling between local and remote backends.

## System design note

The adapter is intentionally thin. It should not duplicate service business
logic, storage validation rules, or provider behavior. Its job is translation:
Python contract to HTTP request, HTTP response back to Python contract.
