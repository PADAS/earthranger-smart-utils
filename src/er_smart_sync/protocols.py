from typing import Any, Protocol, runtime_checkable

from .state import SyncState


@runtime_checkable
class MessagePublisher(Protocol):
    """Publishes messages to a topic (e.g. Google Pub/Sub)."""

    def publish(
        self, topic: str, data: dict, extra: dict[str, str] | None = None
    ) -> None: ...


@runtime_checkable
class FileStorage(Protocol):
    """Stores and retrieves files (e.g. Google Cloud Storage)."""

    def check_exists(self, file_name: str) -> bool: ...

    def upload(self, file: bytes, file_name: str) -> str: ...


@runtime_checkable
class StateStore(Protocol):
    """Persists sync state (last poll timestamps) between runs."""

    def get_last_poll(self, integration_id: str) -> SyncState: ...

    def set_last_poll(self, integration_id: str, state: SyncState) -> None: ...


@runtime_checkable
class TracingProvider(Protocol):
    """Provides distributed tracing spans."""

    def start_span(self, name: str, kind: str = "producer") -> Any:
        """Return a context manager that yields a span-like object."""
        ...

    def build_context_headers(self) -> dict: ...
