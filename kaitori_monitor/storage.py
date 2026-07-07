"""監視対象と価格履歴の SQLite ストレージ。"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS targets(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id TEXT NOT NULL UNIQUE,
    jan TEXT,
    name TEXT NOT NULL,
    query TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS price_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    price INTEGER,
    status TEXT NOT NULL DEFAULT 'ok',
    fetched_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_price_history_target
    ON price_history(target_id, id);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class DuplicateTargetError(Exception):
    """同じ商品が既に監視対象に登録されている。"""


@dataclass
class Target:
    id: int
    class_id: str
    jan: str | None
    name: str
    query: str


class Storage:
    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # bot からは asyncio.to_thread 経由で呼ばれるためスレッドチェックを無効化し、
        # 代わりにロックで直列化する
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # --- 監視対象 ---

    def add_target(
        self, class_id: str, jan: str | None, name: str, query: str
    ) -> Target:
        with self._lock, self._conn:
            try:
                cur = self._conn.execute(
                    "INSERT INTO targets(class_id, jan, name, query) VALUES(?,?,?,?)",
                    (class_id, jan, name, query),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateTargetError(name) from exc
            return Target(cur.lastrowid, class_id, jan, name, query)

    def has_target(self, class_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM targets WHERE class_id = ?", (class_id,)
        )
        return cur.fetchone() is not None

    def has_target_jan(self, jan: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM targets WHERE jan = ?", (jan,))
        return cur.fetchone() is not None

    def list_targets(self) -> list[Target]:
        cur = self._conn.execute(
            "SELECT id, class_id, jan, name, query FROM targets ORDER BY id"
        )
        return [Target(**dict(row)) for row in cur.fetchall()]

    def remove_target(self, target_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM targets WHERE id = ?", (target_id,)
            )
            return cur.rowcount > 0

    # --- 価格履歴 ---

    def record_price(
        self, target_id: int, price: int | None, status: str = "ok"
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO price_history(target_id, price, status) VALUES(?,?,?)",
                (target_id, price, status),
            )

    def last_ok_price(self, target_id: int) -> int | None:
        cur = self._conn.execute(
            "SELECT price FROM price_history"
            " WHERE target_id = ? AND status = 'ok' AND price IS NOT NULL"
            " ORDER BY id DESC LIMIT 1",
            (target_id,),
        )
        row = cur.fetchone()
        return row["price"] if row else None

    # --- 設定 ---

    def get_setting(self, key: str) -> str | None:
        cur = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
