"""HTTP-backed cloud storage adapter package.

Importing this package registers the service adapter with the interface's DI
system as a side-effect.
"""

from cloud_storage_client_api.factory import register_client

from aws_client_adapter.service_adapter import get_client_impl

register_client(get_client_impl)
