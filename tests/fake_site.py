"""An ER site in memory: the event categories, version 2 event types and
choices a data model push reads and writes. A test helper, not a test file."""

from __future__ import annotations

import copy
import itertools


class FakeSite:
    def __init__(self):
        self._categories: dict[str, dict] = {}
        self._types: dict[str, dict] = {}
        self._choices: dict[str, dict] = {}
        self._ids = itertools.count(1)

    # what a test looks at
    def event_types(self) -> dict:
        return copy.deepcopy(self._types)

    def categories(self) -> set:
        return set(self._categories)

    def choice_fields(self) -> set:
        return {c["field"] for c in self._choices.values()}

    def choices(self, field: str) -> dict:
        return {
            c["value"]: dict(c) for c in self._choices.values() if c["field"] == field
        }

    def move(self, value: str, category: str) -> None:
        self._types[value]["category"] = category
        self._categories.setdefault(category, {"value": category, "display": category})

    def clone(self) -> FakeSite:
        return copy.deepcopy(self)

    # event categories and types
    def get_event_categories(self, *a, **k):
        return [dict(c) for c in self._categories.values()]

    def post_event_category(self, data=None, **k):
        self._categories[data["value"]] = dict(data)
        return dict(data)

    def get_event_types(self, *a, version="v1", **k):
        if version != "v2":
            return []
        return copy.deepcopy(list(self._types.values()))

    def post_event_type(self, event_type=None, version="v1", **k):
        if event_type["value"] in self._types:
            raise RuntimeError("duplicate key value violates unique constraint")
        rec = {
            "is_active": True,
            **copy.deepcopy(event_type),
            "id": f"et-{next(self._ids)}",
        }
        self._types[rec["value"]] = rec
        return dict(rec)

    def patch_event_type(self, event_type=None, version="v1", **k):
        rec = next(t for t in self._types.values() if t["id"] == event_type["id"])
        rec.update(copy.deepcopy(event_type))
        return dict(rec)

    # choices
    def _get(self, path=None, params=None, **k):
        params = params or {}
        rows = [
            dict(c) for c in self._choices.values() if c["field"] == params.get("field")
        ]
        return {"count": len(rows), "next": None, "results": rows}

    def _post(self, path=None, payload=None, **k):
        rec = {**payload, "id": f"ch-{next(self._ids)}"}
        self._choices[rec["id"]] = rec
        return dict(rec)

    def _patch(self, path=None, payload=None, **k):
        rec = self._choices[path.rsplit("/", 1)[-1]]
        rec.update(payload)
        return dict(rec)

    def __getattr__(self, name):
        if name.startswith("get_"):
            return lambda *a, **k: []
        raise AttributeError(name)
