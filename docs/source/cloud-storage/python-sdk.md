# Python SDK Usage

The Python SDK path is the easiest way to build on storage. Callers depend on
the external `cloud_storage_api.CloudStorageClient` contract and choose a
factory based on whether they want local S3 access or HTTP-backed service
access.

## Local S3 client

```python
from aws_client_impl import get_client_impl

client = get_client_impl()
```

Environment:

```shell
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
```

## HTTP-backed adapter

```python
from aws_client_adapter import get_client_impl

client = get_client_impl()
```

Environment:

```shell
export CLOUD_STORAGE_SERVICE_BASE_URL="http://localhost:8000"
export API_KEY="dev-storage-api-key"
```

## Upload a file

```python
from aws_client_impl import get_client_impl

client = get_client_impl()
info = client.upload_file(
    container="my-bucket",
    local_path="reports/april.csv",
    remote_path="reports/2026/april.csv",
)
print(info.object_name)
```

Behavior:

1. Validate `container`.
2. Validate `remote_path`.
3. Check local file readability.
4. Use multipart upload if the file is above 100 MiB.
5. Fetch final metadata with `head_object`.
6. Return `ObjectInfo`.

## Upload a file-like object

```python
from pathlib import Path

from aws_client_impl import get_client_impl

client = get_client_impl()
with Path("notes.txt").open("rb") as handle:
    info = client.upload_obj("my-bucket", handle, "notes.txt")
```

File-like objects must be readable and binary. Text-mode objects raise
`InvalidFileObjectError`.

## List files

```python
from aws_client_impl import get_client_impl

client = get_client_impl()
items = client.list_files("my-bucket", "reports/")
for item in items:
    print(item.object_name, item.size_bytes)
```

The result is sorted by object name.

## Download a file

```python
from aws_client_impl import get_client_impl

client = get_client_impl()
info = client.download_file(
    container="my-bucket",
    object_name="reports/2026/april.csv",
    file_name="/tmp/april.csv",
)
```

The method returns metadata for the remote object after writing local bytes.
If the local destination cannot be written, it raises `LocalFileAccessError`.

## Get metadata

```python
from aws_client_impl import get_client_impl

client = get_client_impl()
info = client.get_file_info("my-bucket", "reports/2026/april.csv")
print(info.data_type, info.size_bytes, info.updated_at)
```

Use this when you need metadata without downloading bytes.

## Delete a file

```python
from aws_client_impl import get_client_impl

client = get_client_impl()
result = client.delete_file("my-bucket", "reports/2026/april.csv")
print(result["deleted"])
```

Missing objects raise `ObjectNotFoundError`.

## Error handling

```python
from cloud_storage_api import (
    AuthenticationError,
    InvalidObjectNameError,
    LocalFileAccessError,
    ObjectNotFoundError,
    StorageBackendError,
)
from aws_client_impl import get_client_impl

client = get_client_impl()

try:
    client.download_file("my-bucket", "reports/april.csv", "/tmp/april.csv")
except ObjectNotFoundError:
    print("The object does not exist.")
except AuthenticationError:
    print("Credentials were rejected.")
except InvalidObjectNameError:
    print("The object key is malformed.")
except LocalFileAccessError:
    print("The local path cannot be written.")
except StorageBackendError:
    print("The provider failed unexpectedly; retry policy belongs at the caller.")
```

## Complete local workflow

```python
from pathlib import Path

from aws_client_impl import get_client_impl

container = "my-bucket"
local_source = Path("/tmp/nimbus-sdk-demo.txt")
local_download = Path("/tmp/nimbus-sdk-downloaded.txt")
remote_path = "docs/sdk-demo.txt"

local_source.write_text("hello from the Python SDK\n", encoding="utf-8")

client = get_client_impl()

uploaded = client.upload_file(container, str(local_source), remote_path)
print("uploaded", uploaded.object_name)

listed = client.list_files(container, "docs/")
print([item.object_name for item in listed])

metadata = client.get_file_info(container, remote_path)
print(metadata.size_bytes)

client.download_file(container, remote_path, str(local_download))
print(local_download.read_text(encoding="utf-8"))

deleted = client.delete_file(container, remote_path)
print(deleted["deleted"])
```

## Complete HTTP-backed workflow

```python
from pathlib import Path

from aws_client_adapter import get_client_impl

container = "my-bucket"
remote_path = "docs/adapter-demo.txt"
local_source = Path("/tmp/nimbus-adapter-demo.txt")

local_source.write_text("hello through the adapter\n", encoding="utf-8")

client = get_client_impl()
client.upload_file(container, str(local_source), remote_path)
print(client.get_file_info(container, remote_path).object_name)
client.delete_file(container, remote_path)
```

Environment:

```shell
export CLOUD_STORAGE_SERVICE_BASE_URL="http://localhost:8000"
export API_KEY="dev-storage-api-key"
```

## Choosing local vs HTTP-backed access

| Use local `aws_client_impl` when | Use `aws_client_adapter` when |
|---|---|
| The process is trusted with AWS credentials. | The process should not hold AWS credentials. |
| You need direct boto3-backed behavior. | You want to call the deployed service. |
| You are writing storage implementation tests. | You are building another service or frontend. |
| Network hop is unnecessary. | You want uniform remote authorization and OpenAPI schema. |
