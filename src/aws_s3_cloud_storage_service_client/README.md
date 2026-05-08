# aws-s3-cloud-storage-service-client

Generated OpenAPI client for `aws_client_service`.

Do not edit this package by hand. Change the FastAPI service, regenerate the
client, then update `aws_client_adapter` and tests as needed.

## Role

This is a generated transport client. It mirrors the FastAPI OpenAPI schema and
is intentionally lower-level than the `CloudStorageClient` contract.

## Public API

Use `AuthenticatedClient` or `Client` plus generated endpoint functions under
`aws_s3_cloud_storage_service_client.api.default`. Most application code should
prefer `aws_client_adapter.get_client_impl()`.

## Dependencies

| Dependency | Why it is here |
| --- | --- |
| `httpx` | HTTP transport |
| `attrs` | Generated model classes |
| `python-dateutil` | Generated date/time parsing |

## When to Use It

Most application code should use `aws_client_adapter`, not this generated
package. Import this directly only for low-level HTTP scripting, debugging, or
adapter internals.

## Example

```python
from aws_s3_cloud_storage_service_client import AuthenticatedClient
from aws_s3_cloud_storage_service_client.api.default import list_files_files_get

client = AuthenticatedClient(
    base_url="http://localhost:8000",
    token="dev-storage-api-key",
)

items = list_files_files_get.sync(
    client=client,
    container="my-bucket",
    prefix="reports/",
)
```

## Regenerate

From a local service:

```shell
SESSION_SECRET_KEY=dev-session-secret \
API_KEY=dev-storage-api-key \
uv run uvicorn aws_client_service.main:app --reload
```

```shell
uvx openapi-python-client generate \
  --url http://localhost:8000/openapi.json \
  --meta uv \
  --output-path src/aws_s3_cloud_storage_service_client
```

From deployment:

```shell
uvx openapi-python-client generate \
  --url https://nimbus-production.onrender.com/openapi.json \
  --meta uv \
  --output-path src/aws_s3_cloud_storage_service_client
```

Then run:

```shell
uv run pytest src/aws_client_adapter/tests/ tests/integration/ -q
```

## Full Docs

See `docs/source/cloud-storage/generated-client.md`.
