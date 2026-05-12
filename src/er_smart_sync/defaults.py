import json
import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Any

from .state import SyncState

logger = logging.getLogger(__name__)


class NullPublisher:
    """Logs messages instead of publishing. Suitable for CLI / dry-run use."""

    def publish(
        self, topic: str, data: dict, extra: dict[str, str] | None = None
    ) -> None:
        logger.info(
            "NullPublisher: would publish to %s (%d bytes)",
            topic,
            len(json.dumps(data, default=str).encode("utf-8")),
        )


class LocalFileStorage:
    """Stores files in a local directory."""

    def __init__(self, directory: str = "/tmp/er-smart-sync-files"):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def check_exists(self, file_name: str) -> bool:
        return os.path.exists(os.path.join(self.directory, file_name))

    def upload(self, file: bytes, file_name: str) -> str:
        path = os.path.join(self.directory, file_name)
        with open(path, "wb") as f:
            f.write(file)
        return f"file://{path}"


class JsonFileStateStore:
    """Persists sync state to a JSON file on disk.

    Holds the full state in memory after the first read so per-event
    checkpointing doesn't re-read the file on every call. Writes go through
    a tempfile + os.replace so an interrupted process can't leave a
    partially-written state file behind.
    """

    def __init__(self, path: str = "/tmp/er-smart-sync-state.json"):
        self.path = path
        self._cache: dict | None = None

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        if os.path.exists(self.path):
            with open(self.path) as f:
                self._cache = json.load(f)
        else:
            self._cache = {}
        return self._cache

    def _save(self, data: dict) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".er-smart-sync-state.", suffix=".json", dir=directory
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, default=str)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get_last_poll(self, integration_id: str) -> SyncState:
        data = self._load()
        state_data = data.get(integration_id, {})
        return SyncState(**state_data) if state_data else SyncState()

    def set_last_poll(self, integration_id: str, state: SyncState) -> None:
        data = self._load()
        data[integration_id] = json.loads(state.json())
        self._save(data)


class DryRunERClient:
    """ERClient wrapper that logs writes instead of performing them.

    Reads (``get_*``) pass through to the wrapped client. Writes
    (``post_*``, ``patch_*``, ``delete_*``) log a line and return a
    sentinel dict so callers expecting a response keep working.
    """

    _WRITE_PREFIXES = ("post_", "patch_", "delete_", "put_")

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        # Pass-through anything we don't intercept (e.g. attributes, login).
        inner_attr = getattr(self._inner, name)
        if not callable(inner_attr):
            return inner_attr
        if not name.startswith(self._WRITE_PREFIXES):
            return inner_attr

        def dry_call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            logger.info(
                "DryRunERClient: would call %s args=%s kwargs=%s",
                name,
                args,
                kwargs,
            )
            return {"id": "dry-run", "dry_run": True}

        return dry_call


class _NullSpan:
    """No-op span that accepts arbitrary attribute/event calls."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def add_event(self, name: str, **kwargs: Any) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class NullTracing:
    """No-op tracing provider."""

    @contextmanager
    def start_span(self, name: str, kind: str = "producer"):
        yield _NullSpan()

    def build_context_headers(self) -> dict:
        return {}
