"""Filesystem-backed raw store.

Chosen as the first implementation because it is inspectable: you can read what
the agent captured with ``cat``. It satisfies :class:`RawStore`, so replacing it
with Postgres or object storage is a constructor change.

Layout::

    data/raw/<scope path>/<source>/<record_id>.json
    data/runs/<run_id>.json
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..canonical import RawRecord
from ..scope import ScopeRef
from .base import RunRecord


class FilesystemRawStore:
    def __init__(self, root: Path) -> None:
        self._raw_dir = Path(root) / "raw"
        self._runs_dir = Path(root) / "runs"
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    # -- records --------------------------------------------------------------

    def _record_path(self, record: RawRecord) -> Path:
        return self._raw_dir / record.scope.to_path() / record.source / f"{record.record_id}.json"

    async def write_records(self, records: list[RawRecord]) -> list[str]:
        return await asyncio.to_thread(self._write_records_sync, records)

    def _write_records_sync(self, records: list[RawRecord]) -> list[str]:
        written: list[str] = []
        for record in records:
            path = self._record_path(record)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Re-runs overwrite in place: record_id is derived from the source's
            # own identity, so the store converges instead of duplicating.
            path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
            written.append(record.record_id)
        return written

    async def list_records(self, scope: ScopeRef, *, limit: int = 100) -> list[RawRecord]:
        return await asyncio.to_thread(self._list_records_sync, scope, limit)

    def _list_records_sync(self, scope: ScopeRef, limit: int) -> list[RawRecord]:
        base = self._raw_dir / scope.to_path()
        if not base.exists():
            return []
        records: list[RawRecord] = []
        for path in sorted(base.rglob("*.json")):
            records.append(RawRecord.model_validate_json(path.read_text(encoding="utf-8")))
            if len(records) >= limit:
                break
        return records

    async def get_record(self, record_id: str) -> RawRecord | None:
        return await asyncio.to_thread(self._get_record_sync, record_id)

    def _get_record_sync(self, record_id: str) -> RawRecord | None:
        for path in self._raw_dir.rglob(f"{record_id}.json"):
            return RawRecord.model_validate_json(path.read_text(encoding="utf-8"))
        return None

    # -- runs -----------------------------------------------------------------

    async def write_run(self, run: RunRecord) -> None:
        await asyncio.to_thread(self._write_run_sync, run)

    def _write_run_sync(self, run: RunRecord) -> None:
        path = self._runs_dir / f"{run.run_id}.json"
        path.write_text(run.model_dump_json(indent=2), encoding="utf-8")

    async def get_run(self, run_id: str) -> RunRecord | None:
        return await asyncio.to_thread(self._get_run_sync, run_id)

    def _get_run_sync(self, run_id: str) -> RunRecord | None:
        path = self._runs_dir / f"{run_id}.json"
        if not path.exists():
            return None
        return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_runs(self, limit: int = 50) -> list[RunRecord]:
        paths = sorted(self._runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [
            RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in paths[:limit]
        ]
