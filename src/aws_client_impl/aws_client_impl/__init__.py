"""AWS client implementation package.

Importing this package registers the S3Client factory with the interface's DI
system as a side-effect. Application code should do:

    import aws_client_impl                                # registers factory
    from cloud_storage_client_api.factory import get_client
    client = get_client()                                 # returns S3Client

The factory function (``get_client_impl``) reads AWS_BUCKET_NAME and
AWS_REGION from environment variables so credentials are never hardcoded.
"""

from cloud_storage_client_api.factory import register_client

from aws_client_impl.s3_client import get_client_impl

# Register the S3 factory at import time. This is the DI wiring: importing
# this package is sufficient to make get_client() return an S3Client.
register_client(get_client_impl)
