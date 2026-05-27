# cloud_storage_client_api

Abstract base class (ABC) defining the contract for a cloud storage client

## Role

This package provides the interface that all cloud storage implementations must follow . It contains no concrete logic, only abstract method signatures. Any implementation (AWS, GCP, Dropbox, etc.) must inherit from this.

## API

`CloudStorageClient` — abstract base class with the following methods:

- `upload_file(local_path, remote_path)` — upload a file to cloud storage
- `download_file(remote_path, local_path)` — download a file from cloud storage
- `list_files(prefix)` — list files in storage, optionally filtered by prefix
- `delete_file(remote_path)` — delete a file from cloud storage

## Dependencies

None. This package is intentionally dependency free to keep the interface clean and portable.

## Usage
```python
from cloud_storage_client_api import CloudStorageClient

client: CloudStorageClient = get_client()
```

Code against this interface, not the implementation directly.