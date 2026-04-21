"""Cloud storage client API package.

Public surface:
- ``CloudStorageClient``: the abstract interface (subclass to implement)
- ``get_client``: factory that returns the registered concrete implementation
- ``register_client``: DI hook called by implementation packages at import time
"""

from cloud_storage_client_api.client import CloudStorageClient as CloudStorageClient
from cloud_storage_client_api.exceptions import CloudStorageError as CloudStorageError
from cloud_storage_client_api.exceptions import (
    ContainerNotFoundError as ContainerNotFoundError,
)
from cloud_storage_client_api.exceptions import (
    InvalidContainerError as InvalidContainerError,
)
from cloud_storage_client_api.exceptions import (
    InvalidFileObjectError as InvalidFileObjectError,
)
from cloud_storage_client_api.exceptions import (
    InvalidObjectNameError as InvalidObjectNameError,
)
from cloud_storage_client_api.exceptions import (
    ObjectNotFoundError as ObjectNotFoundError,
)
from cloud_storage_client_api.exceptions import (
    StorageBackendError as StorageBackendError,
)
from cloud_storage_client_api.factory import get_client as get_client
from cloud_storage_client_api.factory import register_client as register_client
