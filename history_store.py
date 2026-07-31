import sqlite3
import threading
from datetime import datetime
from pathlib import Path


class HistoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    actions INTEGER NOT NULL DEFAULT 0,
                    detail TEXT NOT NULL DEFAULT ''
                )
                """
            )

    def start_run(self, task):
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO task_runs(task, started_at) VALUES (?, ?)", (str(task), now)
            )
            return cursor.lastrowid

    def update_run(self, run_id, actions=None, detail=None):
        if not run_id:
            return
        fields, values = [], []
        if actions is not None:
            fields.append("actions = ?")
            values.append(max(0, int(actions)))
        if detail is not None:
            fields.append("detail = ?")
            values.append(str(detail))
        if not fields:
            return
        values.append(int(run_id))
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE task_runs SET {', '.join(fields)} WHERE id = ?", values
            )

    def finish_run(self, run_id, status, detail=None):
        if not run_id:
            return
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            if detail is None:
                connection.execute(
                    "UPDATE task_runs SET ended_at = ?, status = ? WHERE id = ?",
                    (now, str(status), int(run_id)),
                )
            else:
                connection.execute(
                    "UPDATE task_runs SET ended_at = ?, status = ?, detail = ? WHERE id = ?",
                    (now, str(status), str(detail), int(run_id)),
                )

    def list_runs(self, limit=200):
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, task, started_at, ended_at, status, actions, detail
                FROM task_runs ORDER BY id DESC LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear(self):
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM task_runs")
