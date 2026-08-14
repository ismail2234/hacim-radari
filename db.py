from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator


class DB:
    def __init__(self, path: str, retention_days: int = 30):
        self.path = path
        self.retention_days = retention_days

        self._local = threading.local()
        self._write_lock = threading.Lock()

        with self._connect() as conn:
            self._init_schema(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=10,
        )

        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")

        return conn

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)

        if conn is None:
            conn = self._connect()
            self._local.conn = conn

        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            conn = self._conn()

            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _init_schema(self, db: sqlite3.Connection) -> None:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS state(
                symbol TEXT PRIMARY KEY,
                sent REAL DEFAULT 0,
                score REAL DEFAULT 0,
                level TEXT DEFAULT 'NONE',
                stage TEXT DEFAULT 'NONE',
                updated REAL DEFAULT 0,
                streak INTEGER DEFAULT 0,
                streak_at REAL DEFAULT 0,
                trap INTEGER DEFAULT 0,
                priority REAL DEFAULT 0
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                ts REAL NOT NULL,
                price REAL NOT NULL,
                score REAL NOT NULL,
                setup REAL DEFAULT 0,
                confirmation REAL DEFAULT 0,
                penalty REAL DEFAULT 0,
                status TEXT NOT NULL,
                max_pct REAL DEFAULT 0,
                min_pct REAL DEFAULT 0,
                c1 REAL,
                c3 REAL,
                c5 REAL,
                c15 REAL,
                entry_quality REAL DEFAULT 0,
                priority REAL DEFAULT 0,
                d30 REAL,
                d90 REAL,
                trade_1m REAL DEFAULT 0,
                trade_5m REAL DEFAULT 0,
                market_momentum REAL DEFAULT 0,
                trap INTEGER DEFAULT 0
            )
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_signals_ts
            ON signals(ts)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts
            ON signals(symbol, ts)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_signals_c15
            ON signals(c15)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_signals_status
            ON signals(status)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_state_sent
            ON state(sent)
            """
        )

        db.commit()

    def get(self, symbol: str):
        conn = self._conn()

        return conn.execute(
            """
            SELECT
                sent,
                score,
                level,
                stage,
                updated,
                streak,
                streak_at,
                trap,
                priority
            FROM state
            WHERE symbol = ?
            """,
            (symbol,),
        ).fetchone()

    def put(
        self,
        symbol: str,
        score: float,
        level: str,
        stage: str,
        sent: float | None = None,
        streak: int | None = None,
        trap: bool | None = None,
        priority: float | None = None,
    ) -> None:
        now = time.time()

        with self._write() as db:
            old = db.execute(
                """
                SELECT
                    sent,
                    streak,
                    streak_at,
                    trap,
                    priority
                FROM state
                WHERE symbol = ?
                """,
                (symbol,),
            ).fetchone()

            old_sent = float(old[0] or 0) if old else 0.0
            old_streak = int(old[1] or 0) if old else 0
            old_streak_at = float(old[2] or 0) if old else 0.0
            old_trap = int(old[3] or 0) if old else 0
            old_priority = float(old[4] or 0) if old else 0.0

            sent_value = (
                float(sent)
                if sent is not None
                else old_sent
            )

            streak_value = (
                int(streak)
                if streak is not None
                else old_streak
            )

            trap_value = (
                int(bool(trap))
                if trap is not None
                else old_trap
            )

            priority_value = (
                float(priority)
                if priority is not None
                else old_priority
            )

            streak_at = (
                now
                if streak is not None
                else old_streak_at
            )

            db.execute(
                """
                INSERT INTO state(
                    symbol,
                    sent,
                    score,
                    level,
                    stage,
                    updated,
                    streak,
                    streak_at,
                    trap,
                    priority
                )
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    sent = excluded.sent,
                    score = excluded.score,
                    level = excluded.level,
                    stage = excluded.stage,
                    updated = excluded.updated,
                    streak = excluded.streak,
                    streak_at = excluded.streak_at,
                    trap = excluded.trap,
                    priority = excluded.priority
                """,
                (
                    symbol,
                    sent_value,
                    score,
                    level,
                    stage,
                    now,
                    streak_value,
                    streak_at,
                    trap_value,
                    priority_value,
                ),
            )

    def update_streak(
        self,
        symbol: str,
        qualified: bool,
        trap: bool = False,
    ) -> int:
        now = time.time()

        with self._write() as db:
            row = db.execute(
                """
                SELECT streak, streak_at
                FROM state
                WHERE symbol = ?
                """,
                (symbol,),
            ).fetchone()

            old_streak = (
                int(row[0] or 0)
                if row
                else 0
            )

            old_time = (
                float(row[1] or 0)
                if row
                else 0.0
            )

            if not qualified:
                streak = 0

            elif (
                old_time > 0
                and now - old_time <= 180
            ):
                streak = old_streak + 1

            else:
                streak = 1

            db.execute(
                """
                INSERT INTO state(
                    symbol,
                    streak,
                    streak_at,
                    trap,
                    updated
                )
                VALUES(?,?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    streak = excluded.streak,
                    streak_at = excluded.streak_at,
                    trap = excluded.trap,
                    updated = excluded.updated
                """,
                (
                    symbol,
                    streak,
                    now,
                    int(bool(trap)),
                    now,
                ),
            )

            return streak

    def can_send(
        self,
        symbol: str,
        level: str,
        cooldown: int,
    ) -> bool:
        row = self.get(symbol)

        if not row:
            return True

        sent = float(row[0] or 0)
        old_level = row[2]

        rank = {
            "BUY": 1,
            "VERY": 2,
        }

        if rank.get(level, 0) > rank.get(old_level, 0):
            return True

        return time.time() - sent >= cooldown

    def create_signal(self, r: dict) -> int:
        now = time.time()

        with self._write() as db:
            cursor = db.execute(
                """
                INSERT INTO signals(
                    symbol,
                    ts,
                    price,
                    score,
                    setup,
                    confirmation,
                    penalty,
                    status,
                    entry_quality,
                    priority,
                    d30,
                    d90,
                    trade_1m,
                    trade_5m,
                    market_momentum,
                    trap
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    r["symbol"],
                    now,
                    r["price"],
                    r["score"],
                    r.get("setup", 0),
                    r.get("confirmation", 0),
                    r.get("penalty", 0),
                    r["status"],
                    r.get("entry_quality", 0),
                    r.get("priority", 0),
                    r.get("d30"),
                    r.get("d90"),
                    r.get("trades_1m", 0),
                    r.get("trades_5m", 0),
                    r.get("market_momentum", 0),
                    int(bool(r.get("trap", False))),
                ),
            )

            return int(cursor.lastrowid)

    def update_outcomes(
        self,
        price_map: dict,
        outcome_window: int,
    ) -> None:
        now = time.time()
        cutoff = now - outcome_window

        with self._write() as db:
            rows = db.execute(
                """
                SELECT
                    id,
                    symbol,
                    ts,
                    price,
                    max_pct,
                    min_pct,
                    c1,
                    c3,
                    c5,
                    c15
                FROM signals
                WHERE ts > ?
                ORDER BY ts ASC
                """,
                (cutoff,),
            ).fetchall()

            for row in rows:
                (
                    signal_id,
                    symbol,
                    timestamp,
                    entry_price,
                    max_pct,
                    min_pct,
                    c1,
                    c3,
                    c5,
                    c15,
                ) = row

                current = price_map.get(symbol)

                if (
                    current is None
                    or entry_price is None
                    or entry_price <= 0
                ):
                    continue

                try:
                    current = float(current)
                except (TypeError, ValueError):
                    continue

                if current <= 0:
                    continue

                change = (
                    (current - entry_price)
                    / entry_price
                    * 100
                )

                updates = {
                    "max_pct": max(
                        float(max_pct or 0),
                        change,
                    ),
                    "min_pct": min(
                        float(min_pct or 0),
                        change,
                    ),
                }

                elapsed = now - timestamp

                if elapsed >= 60 and c1 is None:
                    updates["c1"] = change

                if elapsed >= 180 and c3 is None:
                    updates["c3"] = change

                if elapsed >= 300 and c5 is None:
                    updates["c5"] = change

                if elapsed >= 900 and c15 is None:
                    updates["c15"] = change

                assignments = ", ".join(
                    f"{column} = ?"
                    for column in updates
                )

                values = list(updates.values())
                values.append(signal_id)

                db.execute(
                    f"""
                    UPDATE signals
                    SET {assignments}
                    WHERE id = ?
                    """,
                    values,
                )

    def cleanup_old_signals(self) -> int:
        cutoff = (
            time.time()
            - self.retention_days * 86400
        )

        with self._write() as db:
            cursor = db.execute(
                """
                DELETE FROM signals
                WHERE ts < ?
                """,
                (cutoff,),
            )

            return max(cursor.rowcount, 0)

    def performance_summary(self) -> list[tuple]:
        conn = self._conn()

        return conn.execute(
            """
            SELECT
                score,
                setup,
                confirmation,
                max_pct,
                min_pct,
                c5,
                c15,
                status,
                entry_quality,
                priority,
                d30,
                d90,
                trade_1m,
                trade_5m,
                market_momentum,
                trap
            FROM signals
            WHERE c15 IS NOT NULL
            ORDER BY ts DESC
            """
        ).fetchall()

    def close(self) -> None:
        conn = getattr(
            self._local,
            "conn",
            None,
        )

        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None
