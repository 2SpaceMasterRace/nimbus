# aws-client-adapter

HTTP-backed implementation of `cloud_storage_api.CloudStorageClient`.

Use this package when your process should call the storage service over HTTP
instead of holding AWS credentials directly.

## Role

This is the remote storage adapter. It preserves the `CloudStorageClient` Python
API while moving execution across the network to `aws_client_service`.

## Public API

| Entry point | Purpose |
| --- | --- |
| `get_client_impl()` | Factory returning a `CloudStorageServiceAdapter` |
| `CloudStorageServiceAdapter` | Implements `CloudStorageClient` over HTTP |

## Dependencies

| Dependency | Why it is here |
| --- | --- |
| `cloud-storage-api` | Interface and domain exceptions to preserve |
| `aws-s3-cloud-storage-service-client` | Generated HTTP client used internally |
| `httpx` | Transport exception types and HTTP client substrate |

## Architecture Position

```text
CloudStorageClient caller
  -> aws_client_adapter.CloudStorageServiceAdapter
  -> aws_s3_cloud_storage_service_client
  -> aws_client_service
  -> aws_client_impl
  -> AWS S3
```

From the caller's perspective, the local and remote clients expose the same
contract. Only the factory import changes.

## Quick Start

```shell
export CLOUD_STORAGE_SERVICE_BASE_URL="http://localhost:8000"
export API_KEY="dev-storage-api-key"
```

```python
from aws_client_adapter import get_client_impl

client = get_client_impl()
items = client.list_files("my-bucket", "reports/")
client.upload_file("my-bucket", "local.csv", "reports/local.csv")
```

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `CLOUD_STORAGE_SERVICE_BASE_URL` | no | `http://127.0.0.1:8000` | Storage service base URL |
| `API_KEY` | yes for protected routes | none | Storage service API key |

If `API_KEY` is unset, the adapter can only reach unauthenticated service
routes. Storage file routes will return `401`.

## Translation Rules

| Contract method | HTTP route |
|---|---|
| `upload_file` / `upload_obj` | `POST /files/{container}/{object_name}` |
| `download_file` | `GET /download`, then local write and metadata fetch |
| `list_files` | `GET /files` |
| `delete_file` | `DELETE /files/{container}/{object_name}` |
| `get_file_info` | `GET /files/{container}/{object_name}/info` |

HTTP and transport failures are translated back into `cloud_storage_api` domain
exceptions so local and remote callers can share error handling.

## Tests

```shell
uv run --package aws-client-adapter pytest src/aws_client_adapter/tests/ -q
uv run pytest tests/integration/test_adapter_integration.py -q
```

## Full Docs

See `docs/source/cloud-storage/service-adapter.md`.
