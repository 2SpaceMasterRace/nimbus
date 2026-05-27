# Generated OpenAPI Client

`aws_s3_cloud_storage_service_client` is generated from the FastAPI OpenAPI
schema. It is a mechanical artifact and should not be edited by hand.

## When to use it

Most application code should not import the generated client directly. Use
`aws_client_adapter` unless you need raw endpoint-level HTTP behavior.

Direct generated-client use is reasonable for:

- contract debugging
- one-off scripts
- integration tests around OpenAPI generation
- adapter implementation internals

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

## Regenerate after API changes

Use the repository helpers from the root. `update_openapi_schema.sh` imports the
FastAPI app and writes an ignored schema snapshot to `build/openapi/`, so a
local server is not required.

```shell
./scripts/update_openapi_schema.sh
./scripts/generate_client.sh
```

To generate from a running service instead, point `SCHEMA_PATH` at a schema you
downloaded from `/openapi.json`, then skip refresh:

```shell
curl -sS http://localhost:8000/openapi.json -o /tmp/nimbus-openapi.json
SCHEMA_PATH=/tmp/nimbus-openapi.json REFRESH_SCHEMA=0 ./scripts/generate_client.sh
```

After regeneration:

```shell
uv run pytest src/aws_client_adapter/tests/ tests/integration/ -q
uv run ruff check src/aws_s3_cloud_storage_service_client src/aws_client_adapter
```

## Review rule

Generated diffs can be large. Review the service route/model changes first,
then scan generated diffs for expected endpoint/model drift. Do not hand-fix
generated code to make tests pass.
