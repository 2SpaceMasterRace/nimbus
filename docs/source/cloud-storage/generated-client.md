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

Start the local service:

```shell
SESSION_SECRET_KEY=dev-session-secret \
API_KEY=dev-storage-api-key \
uv run uvicorn aws_client_service.main:app --reload
```

Generate from local OpenAPI:

```shell
uvx openapi-python-client generate \
  --url http://localhost:8000/openapi.json \
  --meta uv \
  --output-path src/aws_s3_cloud_storage_service_client
```

Generate from deployed OpenAPI:

```shell
uvx openapi-python-client generate \
  --url https://ospsd-team-2.fly.dev/openapi.json \
  --meta uv \
  --output-path src/aws_s3_cloud_storage_service_client
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
