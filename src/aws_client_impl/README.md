# aws_client_impl

AWS S3 implementation of `cloud_storage_client_api`.

## Role

This package is the concrete adapter around `boto3`. Importing it registers the
local S3-backed implementation with the abstract factory.

## API

`S3Client(region_name="us-east-1")` implements the `CloudStorageClient`
contract using explicit container/bucket arguments per call:

- `upload_file(container, local_path, remote_path)`
- `upload_obj(container, file_obj, remote_path)`
- `download_file(container, object_name, file_name)`
- `list_files(container, prefix="")`
- `delete_file(container, object_name)`

It also exposes S3-specific helpers not present on the abstract interface:

- `create_bucket(bucket_name, region_name=None)`
- `delete_bucket(bucket_name)`
- multipart helpers for large uploads

## Dependencies

- `boto3`
- `cloud-storage-client-api`

## Configuration

The implementation reads AWS credentials and region from environment variables:

```shell
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
```

`AWS_BUCKET_NAME` is no longer required by the implementation itself. It is
only used by the repository's `main.py` demo entry point.

## Usage

```python
import aws_client_impl
from cloud_storage_client_api.factory import get_client

client = get_client()
client.upload_file("my-bucket", "local/report.csv", "reports/report.csv")
```
