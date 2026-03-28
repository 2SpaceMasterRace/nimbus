# Getting Started

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — the project's package manager

## Installation

Clone the repo and install all workspace packages in one step:

```console
$ git clone git@github.com:ospsd-team-2/ospsd-team-2.git
$ cd ospsd-team-2
$ uv sync --all-packages
```

## AWS Credentials

Set these environment variables before running anything that touches real S3:

```console
$ export AWS_ACCESS_KEY_ID="your_access_key_id"
$ export AWS_SECRET_ACCESS_KEY="your_secret_access_key"
$ export AWS_REGION="us-east-1"
$ export AWS_BUCKET_NAME="your-bucket-name"
```

Unit and integration tests are fully mocked — no credentials needed for those.

## Basic Usage

### Python client

```python
import aws_client_impl                              # registers S3 via dependency injection
from cloud_storage_client_api.factory import get_client

client = get_client()                              # returns S3Client, typed as CloudStorageClient
files  = client.list_files("")                     # list all objects in your bucket
client.upload_file("local/data.csv", "data.csv")  # upload a file
client.download_file("my-bucket", "data.csv", "local/copy.csv")  # download it back
```

Or run the bundled example:

```console
$ uv run python main.py
```

### HTTP service

Start the FastAPI server:

```console
$ uv run uvicorn aws_client_service.main:app --reload
```

Upload a file via HTTP:

```console
$ curl -X POST "http://localhost:8000/files/my-bucket/data.csv" \
    -F "file=@/path/to/local/data.csv"
```

Download a file via HTTP:

```console
$ curl "http://localhost:8000/download?bucket_name=my-bucket&object_name=data.csv" \
    --output data.csv
```

Delete an object via HTTP:

```console
$ curl -X DELETE "http://localhost:8000/files/my-bucket/data.csv"
```

See {doc}`api` for the full endpoint reference.

## Next Steps

- Read {doc}`api` for the full HTTP and Python API reference.
- Read {doc}`DESIGN` for architecture decisions and context.
- See {doc}`CONTRIBUTING` if you'd like to contribute.
