from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager


class DB:

    def __init__(self, path: str, retention_days: int = 30):
        self.path = path
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._local = threading.local()

        with self._connect() as db:
            self._schema(db)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _conn(self):
        db = getattr(self._local, "db", None)

        if db is None:
            db = self._connect()
            self._local.db = db

        return db

    @contextmanager
    def _write(self):
        with self._lock:
            db = self._conn()

            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def _schema(self, db):

        db.execute("""
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
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                ts REAL,
                price REAL,
                score REAL,
                setup REAL DEFAULT 0,
                confirmation REAL DEFAULT 0,
                penalty REAL DEFAULT 0,
                status TEXT,
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
        """)

        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sig_symbol "
            "ON signals(symbol)"
        )

        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sig_ts "
            "ON signals(ts)"
        )

        db.commit()

    # ---------------------------------------------------------
    # STATE
    # ---------------------------------------------------------

    def get(self, symbol):

        return self._conn().execute("""
            SELECT sent, score, level, stage, updated,
                   streak, streak_at, trap, priority
            FROM state
            WHERE symbol=?
        """, (symbol,)).fetchone()

    def put(
        self,
        symbol,
        score,
        level,
        stage,
        sent=None,
        streak=None,
        trap=None,
        priority=None,
    ):

        with self._write() as db:

            old = db.execute("""
                SELECT sent, streak, trap, priority
                FROM state
                WHERE symbol=?
            """, (symbol,)).fetchone()

            now = time.time()

            old_sent = old[0] if old else 0
            old_streak = old[1] if old else 0
            old_trap = old[2] if old else 0
            old_priority = old[3] if old else 0

            db.execute("""
                INSERT INTO state(
                    symbol, sent, score, level, stage,
                    updated, streak, streak_at, trap, priority
                )
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score,
                    level=excluded.level,
                    stage=excluded.stage,
                    updated=excluded.updated,
                    streak=excluded.streak,
                    streak_at=excluded.streak_at,
                    trap=excluded.trap,
                    priority=excluded.priority
            """, (
                symbol,
                now if sent is not None else old_sent,
                score,
                level,
                stage,
                now,
                old_streak if streak is None else streak,
                now,
                old_trap if trap is None else int(trap),
                old_priority if priority is None else priority,
            ))

    # ---------------------------------------------------------
    # STREAK
    # ---------------------------------------------------------

    def update_streak(
        self,
        symbol,
        qualified,
        trap=False,
    ):

        now = time.time()

        with self._write() as db:

            row = db.execute("""
                SELECT streak, streak_at
                FROM state
                WHERE symbol=?
            """, (symbol,)).fetchone()

            old_streak = int(row[0] or 0) if row else 0
            old_time = float(row[1] or 0) if row else 0

            if not qualified:
                streak = 0
            elif old_time and now - old_time <= 180:
                streak = old_streak + 1
            else:
                streak = 1

            db.execute("""
                INSERT INTO state(
                    symbol, streak, streak_at,
                    trap, updated
                )
                VALUES(?,?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    streak=excluded.streak,
                    streak_at=excluded.streak_at,
                    trap=excluded.trap,
                    updated=excluded.updated
            """, (
                symbol,
                streak,
                now,
                int(trap),
                now,
            ))

            return streak

    # ---------------------------------------------------------
    # TEKRAR SİNYAL KONTROLÜ
    # ---------------------------------------------------------

    def can_send(
        self,
        symbol,
        level,
        cooldown,
    ):

        row = self.get(symbol)

        if not row:
            return True

        sent = float(row[0] or 0)
        old_level = row[2]

        rank = {
            "PASS": 0,
            "ONCU": 1,
            "BUY": 2,
            "VERY": 3,
        }

        return (
            time.time() - sent >= cooldown
            or rank.get(level, 0)
            > rank.get(old_level, 0)
        )

    # ---------------------------------------------------------
    # SON SİNYAL
    # ---------------------------------------------------------

    def get_last_signal(self, symbol):

        row = self._conn().execute("""
            SELECT
                ts,
                price,
                score,
                status,
                entry_quality,
                priority
            FROM signals
            WHERE symbol=?
            ORDER BY ts DESC
            LIMIT 1
        """, (symbol,)).fetchone()

        if not row:
            return None

        return {
            "ts": row[0],
            "price": row[1],
            "score": row[2],
            "status": row[3],
            "entry_quality": row[4],
            "priority": row[5],
        }

    # ---------------------------------------------------------
    # SİNYAL KAYDI
    # ---------------------------------------------------------

    def create_signal(self, r):

        with self._write() as db:

            cur = db.execute("""
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
            """, (
                r["symbol"],
                time.time(),
                r["price"],
                r["score"],
                0,
                r.get("criteria_count", 0),
                0,
                r["status"],
                r.get("entry_quality", 0),
                r.get("priority", 0),
                r.get("d30"),
                r.get("d90"),
                r.get("trades_1m", 0),
                r.get("trades_5m", 0),
                r.get("market_momentum", 0),
                int(r.get("trap", False)),
            ))

            return cur.lastrowid

    # ---------------------------------------------------------
    # SONUÇLAR
    # ---------------------------------------------------------

    def update_outcomes(
        self,
        price_map,
        outcome_window,
    ):

        now = time.time()

        with self._write() as db:

            rows = db.execute("""
                SELECT
                    id, symbol, ts, price,
                    max_pct, min_pct,
                    c1, c3, c5, c15
                FROM signals
                WHERE ts > ?
            """, (
                now - outcome_window,
            )).fetchall()

            for row in rows:

                (
                    sid,
                    symbol,
                    ts,
                    price,
                    max_pct,
                    min_pct,
                    c1,
                    c3,
                    c5,
                    c15,
                ) = row

                current = price_map.get(symbol)

                if not current or not price:
                    continue

                change = (
                    (current - price)
                    / price * 100
                )

                updates = {
                    "max_pct": max(max_pct or 0, change),
                    "min_pct": min(min_pct or 0, change),
                }

                elapsed = now - ts

                if elapsed >= 60 and c1 is None:
                    updates["c1"] = change

                if elapsed >= 180 and c3 is None:
                    updates["c3"] = change

                if elapsed >= 300 and c5 is None:
                    updates["c5"] = change

                if elapsed >= 900 and c15 is None:
                    updates["c15"] = change

                if not updates:
                    continue

                sql = ", ".join(
                    f"{key}=?"
                    for key in updates
                )

                db.execute(
                    f"UPDATE signals SET {sql} WHERE id=?",
                    (*updates.values(), sid),
                )

    # ---------------------------------------------------------
    # TEMİZLİK
    # ---------------------------------------------------------

    def cleanup_old_signals(self):

        cutoff = (
            time.time()
            - self.retention_days * 86400
        )

        with self._write() as db:

            cur = db.execute(
                "DELETE FROM signals WHERE ts < ?",
                (cutoff,),
            )

            return cur.rowcount

    # ---------------------------------------------------------
    # PERFORMANS
    # ---------------------------------------------------------

    def performance_summary(self):

        return self._conn().execute("""
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
        """).fetchall()
