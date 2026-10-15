"""What import-patrols has written, per site and conservation area: the
stages each patrol reached, the ER ids it wrote, and the subjects it
reactivated for the run. A JSON file beside the bundle (the spec's
StateStore only knows poll timestamps)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

STAGES = ("patrol", "events", "tracks")


class ImportLedger:
    def __init__(self, path: Path, site_key: str, read_only: bool = False):
        self._lock = threading.RLock()
        self.path = Path(path)
        self.read_only = read_only
        self._data = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {}
        )
        self._site = self._data.setdefault(site_key, {"patrols": {}, "reactivated": []})

    def _patrol(self, patrol_uuid: str) -> dict:
        return self._site["patrols"].setdefault(patrol_uuid, {"stages": [], "ids": {}})

    def stages(self, patrol_uuid: str) -> set:
        with self._lock:
            return set(self._site["patrols"].get(patrol_uuid, {}).get("stages", []))

    def ids(self, patrol_uuid: str) -> dict:
        with self._lock:
            return dict(self._site["patrols"].get(patrol_uuid, {}).get("ids", {}))

    def done(self, patrol_uuid: str, stage: str, **ids) -> None:
        with self._lock:
            p = self._patrol(patrol_uuid)
            if stage not in p["stages"]:
                p["stages"].append(stage)
            p["ids"].update(ids)
            self._save()

    def add_id(self, patrol_uuid: str, key: str, value: str) -> None:
        with self._lock:
            p = self._patrol(patrol_uuid)
            p["ids"].setdefault(key, [])
            if value not in p["ids"][key]:
                p["ids"][key].append(value)
            self._save()

    def drop_id(self, patrol_uuid: str, key: str) -> None:
        """Forget a list once its stage is done, so the file stays small."""
        with self._lock:
            p = self._site["patrols"].get(patrol_uuid)
            if p and p["ids"].pop(key, None) is not None:
                self._save()

    def reactivated(self) -> list:
        with self._lock:
            return list(self._site["reactivated"])

    def set_reactivated(self, ids: list) -> None:
        with self._lock:
            self._site["reactivated"] = list(ids)
            self._save()

    def _save(self) -> None:
        if self.read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f, indent=1)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
