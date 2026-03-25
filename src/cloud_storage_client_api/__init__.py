"""Cloud storage client API package.

Public surface:
- ``CloudStorageClient``: the abstract interface (subclass to implement)
- ``get_client``: factory that returns the registered concrete implementation
- ``register_client``: DI hook called by implementation packages at import time
"""

from cloud_storage_client_api.client import CloudStorageClient as CloudStorageClient
from cloud_storage_client_api.factory import get_client as get_client
from cloud_storage_client_api.factory import register_client as register_client
