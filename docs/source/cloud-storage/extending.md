# Extending Cloud Storage

This page is for developers adding new storage capabilities or a new provider.

## Add a new provider implementation

Create a new workspace package, for example `gcp_client_impl`, that depends on
the external `cloud_storage_api` package.

Minimum contract:

```python
from cloud_storage_api import CloudStorageClient, ObjectInfo


class GCSClient(CloudStorageClient):
    def upload_file(self, container: str, local_path: str, remote_path: str) -> ObjectInfo:
        ...
```

Factory:

```python
def get_client_impl(*, interactive: bool = False) -> GCSClient:
    return GCSClient(...)
```

Checklist:

- Keep provider SDK imports inside the new implementation package.
- Implement every `CloudStorageClient` method.
- Translate provider errors into `cloud_storage_api` domain exceptions.
- Return `ObjectInfo` and `DeleteResult`, not provider-native response objects.
- Add unit tests with mocked provider calls.
- Add integration tests proving factory and interface compliance.
- Document required environment variables.

## Add a new storage route

Route changes affect multiple layers.

Checklist:

1. Add or update the FastAPI route in `aws_client_service`.
2. Add request/response models with explicit Pydantic fields.
3. Map domain exceptions to HTTP status codes.
4. Add service tests.
5. Regenerate `aws_s3_cloud_storage_service_client`.
6. Update `aws_client_adapter` if the route belongs in the Python contract.
7. Add adapter tests and integration tests.
8. Update `docs/source/cloud-storage/http-api.md` and `docs/source/api.md`.

Ask before changing public API shape or the `CloudStorageClient` contract.

## Add storage behavior for Nimbus tools

There are two tool surfaces:

| Tool surface | File | Use when |
|---|---|---|
| Model-exposed tools | `openrouter_ai_client_impl/cloud_storage_tools.py` | The model calls a typed storage tool through the AI client. |
| Runtime actions | `nimbus_runtime/runtime.py` | The runtime should enforce safety before or instead of model execution. |

Examples:

- Listing files can be model-exposed.
- Delete confirmation belongs in runtime guardrails.
- Attachment upload belongs in runtime because it validates actual bytes from
  the wrapper.

## Design review questions

Before merging storage changes, answer:

- What is the public contract?
- Which package owns the state?
- Which failures are translated and where?
- Is the operation idempotent?
- Can the operation be retried safely?
- Does the adapter preserve local/remote behavior?
- Does the OpenAPI client need regeneration?
- Are docs and examples updated?
