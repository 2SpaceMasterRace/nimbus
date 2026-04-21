# cloud_storage_client_api

Provider-agnostic abstract contract and domain exceptions for cloud storage.

## Role

This package defines the public interface that every implementation must honor.
It contains no FastAPI, boto3, HTTP, or provider-specific types.

## API

`CloudStorageClient` exposes container-scoped object operations:

- `upload_file(container, local_path, remote_path)`
- `upload_obj(container, file_obj, remote_path)`
- `download_file(container, object_name, file_name)`
- `list_files(container, prefix="")`
- `delete_file(container, object_name)`

The package also exports typed domain exceptions:

- `InvalidContainerError`
- `InvalidObjectNameError`
- `InvalidFileObjectError`
- `ObjectNotFoundError`
- `StorageBackendError`

## Dependencies

None. This package is intentionally framework-free and implementation-free.

## Usage

```python
from cloud_storage_client_api.factory import get_client

client = get_client()
files = client.list_files("my-bucket", "reports/")
```

Callers should depend on this package, not on a concrete implementation.
