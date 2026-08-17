from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager


class DB:
    def __init__(
        self,
        path: str,
        retention_days: int = 30,
    ):
        self.path = path
        self.retention_days = retention_days

        # Her thread kendi SQLite bağlantısını kullanır.
        self._local = threading.local()

        # SQLite'da yazma işlemleri tek tek yapılır.
        self._write_lock = threading.Lock()

        # Ana bağlantıyla şema oluştur.
        db = self._connect()
        try:
            self._init_schema(db)
        finally:
            db.close()

    # ============================================================
    # CONNECTION
    # ============================================================

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=5,
        )

        conn.execute(
            "PRAGMA journal_mode=WAL"
        )
        conn.execute(
            "PRAGMA synchronous=NORMAL"
        )
        conn.execute(
            "PRAGMA busy_timeout=5000"
        )
        conn.execute(
            "PRAGMA foreign_keys=ON"
        )

        return conn

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(
            self._local,
            "conn",
            None,
        )

        if conn is None:
            conn = self._connect()
            self._local.conn = conn

        return conn

    @contextmanager
    def _write(self):
        with self._write_lock:
            conn = self._conn()

            try:
                yield conn
                conn.commit()

            except Exception:
                conn.rollback()
                raise

    # ============================================================
    # SCHEMA
    # ============================================================

    def _init_schema(
        self,
        db: sqlite3.Connection,
    ) -> None:

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
            """
        )

        # Performans ve outcome sorgularını hızlandırır.
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_signals_ts
            ON signals(ts)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_signals_symbol
            ON signals(symbol)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_signals_c15
            ON signals(c15)
            """
        )

        db.commit()

    # ============================================================
    # STATE - OKUMA
    # ============================================================

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
            WHERE symbol=?
            """,
            (symbol,),
        ).fetchone()

    # ============================================================
    # STATE - YAZMA
    # ============================================================

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
    ) -> None:

        with self._write() as db:

            old = db.execute(
                """
                SELECT
                    sent,
                    streak,
                    trap,
                    priority
                FROM state
                WHERE symbol=?
                """,
                (symbol,),
            ).fetchone()

            now = time.time()

            old_sent = (
                old[0]
                if old
                else 0
            )

            old_streak = (
                old[1]
                if old
                else 0
            )

            old_trap = (
                old[2]
                if old
                else 0
            )

            old_priority = (
                old[3]
                if old
                else 0
            )

            sent_time = (
                now
                if sent is not None
                else old_sent
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
                VALUES(
                    ?,?,?,?,?,?,?,?,?,?
                )

                ON CONFLICT(symbol)
                DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score,
                    level=excluded.level,
                    stage=excluded.stage,
                    updated=excluded.updated,
                    streak=excluded.streak,
                    streak_at=excluded.streak_at,
                    trap=excluded.trap,
                    priority=excluded.priority
                """,
                (
                    symbol,
                    sent_time,
                    score,
                    level,
                    stage,
                    now,

                    (
                        old_streak
                        if streak is None
                        else streak
                    ),

                    now,

                    (
                        old_trap
                        if trap is None
                        else int(trap)
                    ),

                    (
                        old_priority
                        if priority is None
                        else priority
                    ),
                ),
            )

    # ============================================================
    # STREAK
    # ============================================================

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
                SELECT
                    streak,
                    streak_at
                FROM state
                WHERE symbol=?
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
                VALUES(
                    ?,?,?,?,?
                )

                ON CONFLICT(symbol)
                DO UPDATE SET
                    streak=excluded.streak,
                    streak_at=excluded.streak_at,
                    trap=excluded.trap,
                    updated=excluded.updated
                """,
                (
                    symbol,
                    streak,
                    now,
                    int(trap),
                    now,
                ),
            )

            return streak

    # ============================================================
    # SİNYAL GÖNDERME KONTROLÜ
    # ============================================================

    def can_send(
        self,
        symbol: str,
        level: str,
        cooldown: int,
    ) -> bool:

        row = self.get(symbol)

        if not row:
            return True

        sent = float(
            row[0] or 0
        )

        old_level = row[2]

        rank = {
            "WATCH": 0,
            "ONCU": 1,
            "BUY": 2,
            "VERY": 3,
        }

        return (
            time.time() - sent >= cooldown
            or rank.get(level, 0)
            > rank.get(old_level, 0)
        )

    # ============================================================
    # SIGNAL KAYDI
    # ============================================================

    def create_signal(
        self,
        r: dict,
    ) -> int:

        with self._write() as db:

            cur = db.execute(
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
                VALUES(
                    ?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?
                )
                """,
                (
                    r["symbol"],
                    time.time(),
                    r.get("price", 0),
                    r.get("score", 0),
                    r.get("setup", 0),
                    r.get("confirmation", 0),
                    r.get("penalty", 0),
                    r.get("status", "PASS"),
                    r.get("entry_quality", 0),
                    r.get("priority", 0),
                    r.get("d30"),
                    r.get("d90"),
                    r.get("trades_1m", 0),
                    r.get("trades_5m", 0),
                    r.get("market_momentum", 0),
                    int(
                        bool(
                            r.get(
                                "trap",
                                False,
                            )
                        )
                    ),
                ),
            )

            return cur.lastrowid

    # ============================================================
    # OUTCOME GÜNCELLEME
    # ============================================================

    def update_outcomes(
        self,
        price_map: dict,
        outcome_window: int,
    ) -> None:

        now = time.time()

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
                """,
                (
                    now - outcome_window,
                ),
            ).fetchall()

            for row in rows:

                (
                    signal_id,
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

                current = price_map.get(
                    symbol
                )

                if (
                    current is None
                    or price is None
                    or price <= 0
                ):
                    continue

                change = (
                    (current - price)
                    / price
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

                elapsed = now - ts

                if (
                    elapsed >= 60
                    and c1 is None
                ):
                    updates["c1"] = change

                if (
                    elapsed >= 180
                    and c3 is None
                ):
                    updates["c3"] = change

                if (
                    elapsed >= 300
                    and c5 is None
                ):
                    updates["c5"] = change

                if (
                    elapsed >= 900
                    and c15 is None
                ):
                    updates["c15"] = change

                if not updates:
                    continue

                clause = ", ".join(
                    f"{key}=?"
                    for key in updates
                )

                values = list(
                    updates.values()
                )

                values.append(
                    signal_id
                )

                db.execute(
                    f"""
                    UPDATE signals
                    SET {clause}
                    WHERE id=?
                    """,
                    values,
                )

    # ============================================================
    # ESKİ SİNYALLERİ TEMİZLE
    # ============================================================

    def cleanup_old_signals(self) -> int:

        cutoff = (
            time.time()
            - self.retention_days * 86400
        )

        with self._write() as db:

            cur = db.execute(
                """
                DELETE FROM signals
                WHERE ts < ?
                """,
                (cutoff,),
            )

            return cur.rowcount

    # ============================================================
    # PERFORMANS
    # ============================================================

    def performance_summary(self):

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
            """
        ).fetchall()

    # ============================================================
    # SON SİNYAL
    # ============================================================

    def get_last_signal(
        self,
        symbol: str,
    ) -> dict | None:

        conn = self._conn()

        row = conn.execute(
            """
            SELECT
                id,
                symbol,
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
            """,
            (symbol,),
        ).fetchone()

        if not row:
            return None

        return {
            "id": row[0],
            "symbol": row[1],
            "ts": row[2],
            "price": row[3],
            "score": row[4],
            "status": row[5],
            "entry_quality": row[6],
            "priority": row[7],
        }
