"""Dependency-injection factory for cloud storage clients.

This module manages the global registry that maps the abstract interface to a
concrete implementation. It is intentionally separate from ``client.py`` so that
the interface definition (``CloudStorageClient``) has zero coupling to the DI
mechanism.

Usage pattern:

1. The implementation package calls ``register_client`` at import time,
   passing a *factory callable* — a zero-argument function that constructs and
   returns a fully initialised ``CloudStorageClient`` instance:

       # in aws_client_impl/__init__.py
       from cloud_storage_client_api.factory import register_client
       register_client(get_client_impl)

2. Application code imports the implementation package (triggering step 1)
   then calls ``get_client()`` to receive a ready-to-use instance:

       import aws_client_impl  # registers S3Client factory as side-effect
       from cloud_storage_client_api.factory import get_client
       client = get_client()

This separation follows the Dependency Inversion Principle: the interface
package defines the contract, the implementation package fulfils it, and
neither depends on the other directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from cloud_storage_client_api.client import CloudStorageClient

# The registered factory callable. Set by register_client(); read by get_client().
# Using a factory callable (rather than the class itself) lets implementations
# control their own construction — reading env vars, setting defaults, etc.
_factory: Callable[[], CloudStorageClient] | None = None


def register_client(factory: Callable[[], CloudStorageClient]) -> None:
    """Register a factory callable for creating CloudStorageClient instances.

    The factory must be a zero-argument callable that returns a fully
    initialised ``CloudStorageClient``. Implementation packages call this
    exactly once at import time.

    Args:
        factory: A callable ``() -> CloudStorageClient``. Typically this is
            a module-level function (not a class) so that the implementation
            controls all construction details (credentials, region, bucket).

    """
    global _factory  # noqa: PLW0603  # The DI registry must be a module-level variable so all callers share the same instance; global assignment is the simplest correct approach here
    _factory = factory


def get_client() -> CloudStorageClient:
    """Return a fresh ``CloudStorageClient`` instance from the registered factory.

    Call this after importing the implementation package so that the factory
    has been registered. Each call produces a new instance.

    Returns:
        A concrete ``CloudStorageClient`` implementation.

    Raises:
        RuntimeError: If no implementation has been registered yet.
            Import the implementation package before calling this function.

    """
    if _factory is None:  # pragma: no cover
        msg = (
            "No CloudStorageClient implementation has been registered. "
            "Import the implementation package (e.g. 'import aws_client_impl') "
            "before calling get_client()."
        )
        raise RuntimeError(msg)
    return _factory()
