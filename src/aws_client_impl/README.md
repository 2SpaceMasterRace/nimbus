# aws-client-impl

AWS S3 implementation of the external `cloud_storage_api.CloudStorageClient`
contract. This is the only package in the workspace that may import `boto3`.

Use this package when the current process is trusted with AWS credentials and
should talk directly to S3.

## Role

This is the local cloud-storage implementation. It adapts AWS S3 and boto3 into
the external `cloud_storage_api` contract and translates provider-specific
failures into domain exceptions.

## Dependencies

| Dependency | Why it is here |
| --- | --- |
| `cloud-storage-api` | External storage contract and domain types |
| `boto3` / `botocore` | AWS S3 SDK and provider exceptions |
| `requests` | OAuth helper HTTP calls |
| `structlog` | Structured operation logging |

## Architecture Position

```text
cloud_storage_api.CloudStorageClient
  -> aws_client_impl.S3Client
  -> boto3
  -> AWS S3
```

`aws_client_service` wraps this implementation over HTTP. `aws_client_adapter`
lets remote callers use the same Python contract without AWS credentials.

## Quick Start

```python
from aws_client_impl import get_client_impl

client = get_client_impl()

client.upload_file("my-bucket", "local/report.csv", "reports/report.csv")
items = client.list_files("my-bucket", "reports/")
client.download_file("my-bucket", "reports/report.csv", "/tmp/report.csv")
info = client.get_file_info("my-bucket", "reports/report.csv")
result = client.delete_file("my-bucket", "reports/report.csv")
```

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `AWS_ACCESS_KEY_ID` | yes for live S3 | none | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | yes for live S3 | none | AWS secret key |
| `AWS_REGION` | no | `us-east-1` | boto3 session region |

`AWS_BUCKET_NAME` is not read by this package. Pass the container/bucket name to
each method.

## Public API

Import the factory in application code:

```python
from aws_client_impl import get_client_impl
```

`get_client_impl(*, interactive: bool = False) -> S3Client`

Contract methods:

| Method | Returns | Notes |
|---|---|---|
| `upload_file(container, local_path, remote_path)` | `ObjectInfo` | Uses multipart above 100 MiB. |
| `upload_obj(container, file_obj, remote_path)` | `ObjectInfo` | File object must be readable and binary. |
| `download_file(container, object_name, file_name)` | `ObjectInfo` | Writes bytes to local filesystem. |
| `list_files(container, prefix)` | `list[ObjectInfo]` | Sorted by `object_name`. |
| `delete_file(container, object_name)` | `DeleteResult` | Missing object raises `ObjectNotFoundError`. |
| `get_file_info(container, object_name)` | `ObjectInfo` | Uses S3 `HeadObject`. |

S3-specific helpers such as multipart upload operations and bucket lifecycle
methods exist for scripts/tests, but they are not part of the provider-neutral
contract.

## Error Translation

| Provider/local condition | Domain exception |
|---|---|
| empty container | `InvalidContainerError` |
| empty key or leading slash | `InvalidObjectNameError` |
| unreadable local file | `LocalFileAccessError` |
| invalid file-like object | `InvalidFileObjectError` |
| missing bucket | `ContainerNotFoundError` |
| missing key | `ObjectNotFoundError` |
| access denied | `AuthenticationError` |
| other S3 failure | `StorageBackendError` |

## Tests

```shell
uv run --package aws-client-impl pytest src/aws_client_impl/tests/ -q
uv run pytest tests/integration/test_client_integration.py -q
```

Live S3 workflows require AWS credentials and should stay opt-in.

## Full Docs

See:

- `docs/source/cloud-storage/python-sdk.md`
- `docs/source/cloud-storage/errors.md`
- `docs/source/cloud-storage/extending.md`
