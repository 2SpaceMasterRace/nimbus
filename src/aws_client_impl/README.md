# aws_client_impl

This package is the AWS S3 concrete implementation of the `cloud_storage_client_api` interface. It uses boto3 to interact with S3.

## Role

This fulfills the contract defined by `cloud_storage_client_api` using AWS S3. Importing this package automatically registers it using Dependency Injection

## API

- `S3Client(bucket_name, region="us-east-1")` concrete class implementing all `CloudStorageClient` methods for AWS S3

Implements all methods from `CloudStorageClient`:

- `upload_file(local_path, key)` uploads a file to S3
- `upload_obj(file_obj, key)` uploads a binary file-like object to S3
- `download_file(bucket_name, object_name, file_name)` downloads an S3 object to a file
- `download_fileobj(bucket_name, object_name, file_name, file_object)` downloads an S3 object to a file-like object
- `list_files(prefix)` lists files in the configured S3 bucket
- `delete_file(bucket_name, object_name)` deletes a file from S3
- `create_bucket(bucket_name, region_name)` creates an S3 bucket
- `delete_bucket(bucket_name)` deletes an S3 bucket

## Dependencies

- `boto3` AWS SDK for Python
- `cloud_storage_client_api` the interface this package implements

## Configuration

The following environment variables are required:
```
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
AWS_BUCKET_NAME=your_bucket_name
```

## Usage

Import this package to inject it, then use the interface:
```python
import aws_client_impl  # registers itself using DI
from cloud_storage_client_api import get_client

client = get_client()
client.upload_file("local/path.txt", "remote/path.txt")
```