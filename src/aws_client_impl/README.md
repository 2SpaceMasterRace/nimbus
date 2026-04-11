# aws_client_impl

AWS S3 implementation of `cloud_storage_api.CloudStorageClient`.

## Role

This package is the concrete adapter around `boto3`. It provides
`get_client_impl()` to create a fully configured S3-backed client.

## API

`S3Client(region_name="us-east-1")` implements the `CloudStorageClient`
contract using explicit container/bucket arguments per call:

- `upload_file(container, local_path, remote_path) -> ObjectInfo`
- `upload_obj(container, file_obj, remote_path) -> ObjectInfo`
- `download_file(container, object_name, file_name) -> ObjectInfo`
- `list_files(container, prefix) -> list[ObjectInfo]`
- `delete_file(container, object_name) -> DeleteResult`
- `get_file_info(container, object_name) -> ObjectInfo`

It also exposes S3-specific helpers not present on the abstract interface:

- `create_bucket(bucket_name, region_name=None)`
- `delete_bucket(bucket_name)`
- multipart helpers for large uploads

## Dependencies

- `boto3`
- `cloud-storage-api` (external, via git)

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
from aws_client_impl.s3_client import get_client_impl

client = get_client_impl()
client.upload_file("my-bucket", "local/report.csv", "reports/report.csv")
```
