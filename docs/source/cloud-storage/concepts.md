# Cloud Storage Concepts

The storage API is shaped around object storage, not filesystems. It avoids
provider-specific nouns in public contracts while still mapping cleanly to S3.

## Containers

A container is the namespace for object operations. In the AWS implementation,
the container is the S3 bucket name.

Every public operation accepts `container` explicitly:

```python
client.list_files("my-bucket", "reports/")
client.upload_file("my-bucket", "local.csv", "reports/local.csv")
```

`aws_client_impl` does not read `AWS_BUCKET_NAME` for normal operations. That
variable is only a convenience for demos and Nimbus tool binding.

## Object names

An object name is the full key inside the container:

```text
reports/2026/april.csv
```

Rules enforced locally:

- object names cannot be empty
- object names cannot start with `/`
- HTTP object names are path parameters and may contain slashes

## Prefixes

`list_files(container, prefix)` returns all objects whose names start with the
prefix. Use `""` for the container root.

The S3 implementation returns `ObjectInfo` entries sorted by `object_name`.
The adapter also sorts results so local and remote behavior match.

## Metadata

`ObjectInfo` is the provider-neutral metadata shape. Fields may be `None`
because not every backend or operation can populate every field.

| Field | Meaning |
|---|---|
| `object_name` | Object key/name |
| `version_id` | Version identifier, if the backend provides one |
| `data_type` | Content type |
| `integrity` | ETag or checksum-like value |
| `encryption` | Server-side encryption mode |
| `storage_tier` | Storage class/tier |
| `size_bytes` | Object size |
| `updated_at` | Last modified timestamp |
| `metadata` | Provider user metadata |

## Delete semantics

`delete_file(container, object_name)` first checks that the object exists, then
calls the provider delete operation. Missing objects raise `ObjectNotFoundError`
instead of returning a successful no-op.

This makes delete behavior explicit for callers and tests.

## Multipart behavior

`aws_client_impl.S3Client` uses multipart upload for local files or file-like
objects above `MULTIPART_THRESHOLD` (`100 MiB`). Multipart helpers exist for
advanced scripts, but ordinary callers should use `upload_file` or `upload_obj`.

Operational invariant: an initiated multipart upload must be completed or
aborted, otherwise AWS can continue charging for uploaded parts.
