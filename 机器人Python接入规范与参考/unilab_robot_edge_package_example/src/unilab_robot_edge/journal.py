from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contracts import CommandRecord, CommandState, StaleSequenceError


class CommandJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(
                parents=True,
                exist_ok=True,
            )
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    action TEXT NOT NULL,
                    state TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS source_heads (
                    source_boot_id TEXT PRIMARY KEY,
                    last_sequence INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._db.execute(
                """
                UPDATE commands
                SET state = ?, error = ?, updated_at = ?
                WHERE state IN (?, ?)
                """,
                (
                    CommandState.UNKNOWN.value,
                    "edge driver restarted while dispatch was in flight",
                    time.time(),
                    CommandState.VALIDATED.value,
                    CommandState.DISPATCHING.value,
                ),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def get(self, command_id: str) -> CommandRecord | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        return None if row is None else _record_from_row(row)

    def create(
        self,
        *,
        command_id: str,
        fingerprint: str,
        action: str,
        request: Mapping[str, Any],
    ) -> CommandRecord:
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                """
                INSERT INTO commands (
                    command_id, fingerprint, action, state, request_json,
                    result_json, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?)
                """,
                (
                    command_id,
                    fingerprint,
                    action,
                    CommandState.VALIDATED.value,
                    _dump(request),
                    "{}",
                    now,
                    now,
                ),
            )
        record = self.get(command_id)
        assert record is not None
        return record

    def update(
        self,
        command_id: str,
        state: CommandState,
        *,
        result: Mapping[str, Any] | None = None,
        error: str = "",
    ) -> CommandRecord:
        with self._lock, self._db:
            self._db.execute(
                """
                UPDATE commands
                SET state = ?, result_json = ?, error = ?, updated_at = ?
                WHERE command_id = ?
                """,
                (
                    state.value,
                    _dump(result or {}),
                    str(error),
                    time.time(),
                    command_id,
                ),
            )
        record = self.get(command_id)
        if record is None:
            raise KeyError(command_id)
        return record

    def accept_sequence(self, source_boot_id: str, sequence: int) -> None:
        if not source_boot_id:
            raise ValueError("source_boot_id must not be empty")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("monotonic_sequence must be a positive integer")
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT last_sequence FROM source_heads WHERE source_boot_id = ?",
                (source_boot_id,),
            ).fetchone()
            if row is not None and sequence <= int(row["last_sequence"]):
                raise StaleSequenceError(
                    f"sequence {sequence} is not newer than "
                    f"{int(row['last_sequence'])} for {source_boot_id}"
                )
            self._db.execute(
                """
                INSERT INTO source_heads (source_boot_id, last_sequence, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source_boot_id) DO UPDATE SET
                    last_sequence = excluded.last_sequence,
                    updated_at = excluded.updated_at
                """,
                (source_boot_id, sequence, time.time()),
            )

    def unresolved_count(self) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS count FROM commands WHERE state = ?",
                (CommandState.UNKNOWN.value,),
            ).fetchone()
        return int(row["count"])


def _record_from_row(row: sqlite3.Row) -> CommandRecord:
    return CommandRecord(
        command_id=str(row["command_id"]),
        fingerprint=str(row["fingerprint"]),
        action=str(row["action"]),
        state=CommandState(str(row["state"])),
        request=json.loads(str(row["request_json"])),
        result=json.loads(str(row["result_json"])),
        error=str(row["error"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _dump(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
