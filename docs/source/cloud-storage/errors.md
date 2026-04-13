# Cloud Storage Errors

Storage errors are deliberately translated at each boundary. Callers should not
need to catch boto3, botocore, httpx, or generated-client exceptions.

## Domain exceptions

| Exception | Meaning | Typical caller response |
|---|---|---|
| `InvalidContainerError` | Container/bucket name is empty or invalid | Fix caller input. |
| `InvalidObjectNameError` | Object key is empty or starts with `/` | Fix caller input. |
| `InvalidFileObjectError` | File-like object is missing, unreadable, or text-mode | Open a binary readable object. |
| `LocalFileAccessError` | Local path cannot be read or written | Check local filesystem path/permissions. |
| `ContainerNotFoundError` | Bucket/container does not exist or cannot be found | Create/configure the bucket or fix container name. |
| `ObjectNotFoundError` | Object key does not exist | Show missing-object state or recover. |
| `AuthenticationError` | Provider or service rejected credentials | Check API key/AWS credentials. |
| `StorageBackendError` | Unexpected provider, transport, or service failure | Retry if safe or surface operational error. |

## boto3 to domain mapping

| boto3 condition | Domain exception |
|---|---|
| `NoSuchBucket` | `ContainerNotFoundError` |
| `NoSuchKey`, `NotFound`, `404` | `ObjectNotFoundError` when key is known |
| `404` without key context | `ContainerNotFoundError` |
| `AccessDenied`, `403` | `AuthenticationError` |
| Other `ClientError` | `StorageBackendError` |

## Service HTTP mapping

| Domain exception | HTTP status |
|---|---|
| `InvalidContainerError` | `400` |
| `InvalidObjectNameError` | `400` |
| `InvalidFileObjectError` | `400` |
| `AuthenticationError` | `401` |
| `ContainerNotFoundError` | `404` |
| `ObjectNotFoundError` | `404` |
| `StorageBackendError` | `502` |

FastAPI request validation errors, such as missing query parameters or malformed
multipart bodies, return `422`.

## Adapter mapping

The adapter converts HTTP responses and httpx failures back to domain
exceptions. This keeps remote and local callers aligned:

```python
from cloud_storage_api import ObjectNotFoundError
from aws_client_adapter import get_client_impl

client = get_client_impl()

try:
    client.get_file_info("my-bucket", "missing.txt")
except ObjectNotFoundError:
    print("The object is missing regardless of whether the backend was local or remote.")
```

## Retry and recovery

| Error | Retry? | Notes |
|---|---|---|
| Validation errors | No | Fix input. |
| Auth errors | No | Fix credentials/secrets. |
| Missing object/container | Usually no | Retry only if a concurrent writer may create it. |
| Local file access | No | Fix path/permissions/disk. |
| Storage backend error | Maybe | Retry with backoff if the operation is safe for your workflow. |
| HTTP transport error through adapter | Maybe | Retry reads freely; retry writes only when the caller can tolerate duplicate/overwrite behavior. |

The codebase does not yet include a generalized storage retry policy. Add one at
a clear boundary when the caller's idempotency semantics are known.
