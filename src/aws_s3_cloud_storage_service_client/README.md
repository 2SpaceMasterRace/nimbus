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

From the repository root, generate a transient schema snapshot and regenerate
this package from it:

```shell
./scripts/update_openapi_schema.sh
./scripts/generate_client.sh
```

To generate from a running service or deployment, download its `/openapi.json`
and skip the local schema refresh:

```shell
curl -sS https://nimbus-production.onrender.com/openapi.json -o /tmp/nimbus-openapi.json
SCHEMA_PATH=/tmp/nimbus-openapi.json REFRESH_SCHEMA=0 ./scripts/generate_client.sh
```

Then run:

```shell
uv run pytest src/aws_client_adapter/tests/ tests/integration/ -q
```

## Full Docs

See `docs/source/cloud-storage/generated-client.md`.
