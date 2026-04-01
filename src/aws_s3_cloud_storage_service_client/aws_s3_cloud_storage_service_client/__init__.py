"""A client library for accessing AWS S3 Cloud Storage Service"""

from .client import AuthenticatedClient, Client

__all__ = (
    "AuthenticatedClient",
    "Client",
)
