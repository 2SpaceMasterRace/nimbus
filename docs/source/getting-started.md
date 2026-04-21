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
from aws_client_impl.s3_client import get_client_impl

client = get_client_impl()                         # returns S3Client, typed as CloudStorageClient
files  = client.list_files("my-bucket", "")        # list all objects in your bucket
client.upload_file("my-bucket", "local/data.csv", "data.csv")
client.download_file("my-bucket", "data.csv", "local/copy.csv")  # download it back
```

Or use the remote service without changing the calling code:

```python
from aws_client_adapter import get_client_impl

client = get_client_impl()
files = client.list_files("my-bucket", "reports/")
```

Or run the bundled example:

```console
$ uv run python main.py
```

### HTTP service

Start the FastAPI server:

```console
$ export API_KEY="replace-me"
$ uv run uvicorn aws_client_service.main:app --reload
```

Protected file endpoints accept either a GitHub OAuth session or an API key in the `X-API-Key` header.

Upload a file via HTTP:

```console
$ curl -X POST "http://localhost:8000/files/my-bucket/data.csv" \
    -H "X-API-Key: $API_KEY" \
    -F "file=@/path/to/local/data.csv"
```

Download a file via HTTP:

```console
$ curl "http://localhost:8000/download?container=my-bucket&object_name=data.csv" \
    -H "X-API-Key: $API_KEY" \
    --output data.csv
```

Delete an object via HTTP:

```console
$ curl -X DELETE \
    -H "X-API-Key: $API_KEY" \
    "http://localhost:8000/files/my-bucket/data.csv"
```

List files in a container:

```console
$ curl -H "X-API-Key: $API_KEY" \
    "http://localhost:8000/files?container=my-bucket&prefix=reports/"
```

The built Sphinx guide is also available from the running app at `/guide/`.

See {doc}`api` for the full endpoint reference.

For more endpoint examples, see {doc}`api`.

## Next Steps

- Read {doc}`api` for the full HTTP and Python API reference.
- Read {doc}`DESIGN` for architecture decisions and context.
- See {doc}`CONTRIBUTING` if you'd like to contribute.
