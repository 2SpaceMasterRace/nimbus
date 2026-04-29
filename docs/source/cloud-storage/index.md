# Cloud Storage

Nimbus's storage vertical is a provider-agnostic object-storage layer with an
AWS S3 implementation, an HTTP service, a generated OpenAPI client, and an
adapter that restores the original Python contract over HTTP.

This section is intentionally resource-oriented: concepts first, then Python
usage, then HTTP endpoints, then errors and extension points.

## Storage at a glance

| Need | Use |
|---|---|
| In-process S3 access | `aws_client_impl.get_client_impl()` |
| HTTP access to the deployed service through the same Python contract | `aws_client_adapter.get_client_impl()` |
| Raw HTTP/curl access | `aws_client_service` endpoints |
| Generated low-level client | `aws_s3_cloud_storage_service_client` |
| New backend | Implement external `cloud_storage_api.CloudStorageClient` |

## Resource model

| Concept | Meaning in this codebase | AWS S3 equivalent |
|---|---|---|
| Container | Top-level storage namespace passed to every operation | Bucket |
| Object name | Provider-neutral stored-object identifier | S3 key |
| Prefix | String filter for listing object names | S3 prefix |
| Object metadata | `ObjectInfo` / `ObjectInfoResponse` | `HeadObject` metadata |
| Delete result | `DeleteResult` / `DeleteResultResponse` | `DeleteObject` response |

## Pages

```{toctree}
:maxdepth: 2

concepts
python-sdk
http-api
service-adapter
generated-client
errors
extending
```

## End-to-end flow

```text
Python caller
  |
  +-- local path --> aws_client_impl.S3Client --> boto3 --> AWS S3
  |
  +-- remote path -> aws_client_adapter.CloudStorageServiceAdapter
                    -> generated OpenAPI client
                    -> aws_client_service FastAPI app
                    -> aws_client_impl.S3Client
                    -> boto3
                    -> AWS S3
```

The local and remote paths intentionally return the same domain objects and
raise the same domain exceptions.
