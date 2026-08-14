from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager


class DB:
    def __init__(self, path: str, retention_days: int = 30):
        self.path = path
        self.retention_days = retention_days
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialize()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)

        if conn is None:
            conn = sqlite3.connect(
                self.path,
                timeout=30,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn

        return conn

    @contextmanager
    def _write(self):
        conn = self._conn()

        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _initialize(self) -> None:
        with self._init_lock:
            conn = self._conn()

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    ts REAL NOT NULL,

                    price REAL NOT NULL,

                    score REAL NOT NULL DEFAULT 0,
                    setup REAL NOT NULL DEFAULT 0,
                    confirmation REAL NOT NULL DEFAULT 0,
                    penalty REAL NOT NULL DEFAULT 0,

                    status TEXT NOT NULL DEFAULT 'BUY',
                    phase TEXT NOT NULL DEFAULT 'CONFIRMED',

                    entry_quality REAL NOT NULL DEFAULT 0,
                    priority REAL NOT NULL DEFAULT 0,

                    streak INTEGER NOT NULL DEFAULT 0,
                    trap INTEGER NOT NULL DEFAULT 0,

                    c5 REAL,
                    c15 REAL,
                    max_pct REAL,
                    min_pct REAL,

                    d30 REAL,
                    d90 REAL,

                    trade_1m INTEGER,
                    trade_5m INTEGER,

                    market_momentum REAL,

                    vr REAL,
                    vr5 REAL,
                    bp REAL,
                    rv REAL,
                    ad REAL,
                    dist REAL,
                    impulse REAL,

                    breakout INTEGER,
                    closed_breakout INTEGER,
                    ema INTEGER,
                    macd INTEGER,
                    squeeze INTEGER,
                    higher_low INTEGER,

                    group_count INTEGER,
                    chg REAL
                );

                CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts
                ON signals(symbol, ts DESC);

                CREATE INDEX IF NOT EXISTS idx_signals_ts
                ON signals(ts DESC);

                CREATE INDEX IF NOT EXISTS idx_signals_status
                ON signals(status);

                CREATE TABLE IF NOT EXISTS streaks (
                    symbol TEXT PRIMARY KEY,
                    streak INTEGER NOT NULL DEFAULT 0,
                    last_qualified INTEGER NOT NULL DEFAULT 0,
                    last_trap INTEGER NOT NULL DEFAULT 0,
                    updated_ts REAL NOT NULL DEFAULT 0
                );
                """
            )

            # Eski DB sürümlerinde yeni sütunlar olmayabilir.
            self._ensure_columns(
                conn,
                {
                    "phase": "TEXT NOT NULL DEFAULT 'CONFIRMED'",
                    "priority": "REAL NOT NULL DEFAULT 0",
                    "penalty": "REAL NOT NULL DEFAULT 0",
                    "d30": "REAL",
                    "d90": "REAL",
                    "trade_1m": "INTEGER",
                    "trade_5m": "INTEGER",
                    "market_momentum": "REAL",
                    "vr": "REAL",
                    "vr5": "REAL",
                    "bp": "REAL",
                    "rv": "REAL",
                    "ad": "REAL",
                    "dist": "REAL",
                    "impulse": "REAL",
                    "breakout": "INTEGER",
                    "closed_breakout": "INTEGER",
                    "ema": "INTEGER",
                    "macd": "INTEGER",
                    "squeeze": "INTEGER",
                    "higher_low": "INTEGER",
                    "group_count": "INTEGER",
                    "chg": "REAL",
                },
            )

            conn.commit()

    @staticmethod
    def _ensure_columns(
        conn: sqlite3.Connection,
        columns: dict[str, str],
    ) -> None:
        existing = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(signals)"
            ).fetchall()
        }

        for name, definition in columns.items():
            if name not in existing:
                conn.execute(
                    f"ALTER TABLE signals ADD COLUMN {name} {definition}"
                )

    def update_streak(
        self,
        symbol: str,
        qualified: bool,
        trap: bool,
    ) -> int:
        now = time.time()

        with self._write() as conn:
            row = conn.execute(
                """
                SELECT streak, last_qualified
                FROM streaks
                WHERE symbol=?
                """,
                (symbol,),
            ).fetchone()

            previous = int(row["streak"]) if row else 0

            if trap or not qualified:
                streak = 0
            else:
                streak = previous + 1

            conn.execute(
                """
                INSERT INTO streaks(
                    symbol,
                    streak,
                    last_qualified,
                    last_trap,
                    updated_ts
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    streak=excluded.streak,
                    last_qualified=excluded.last_qualified,
                    last_trap=excluded.last_trap,
                    updated_ts=excluded.updated_ts
                """,
                (
                    symbol,
                    streak,
                    1 if qualified else 0,
                    1 if trap else 0,
                    now,
                ),
            )

        return streak

    def can_send(
        self,
        symbol: str,
        status: str,
        cooldown: int,
    ) -> bool:
        cutoff = time.time() - cooldown

        conn = self._conn()

        row = conn.execute(
            """
            SELECT 1
            FROM signals
            WHERE symbol=?
              AND status=?
              AND ts >= ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (symbol, status, cutoff),
        ).fetchone()

        return row is None

    def put(
        self,
        symbol: str,
        score: float,
        status: str,
        phase: str,
        *,
        sent: float | None = None,
        streak: int = 0,
        trap: bool = False,
        priority: float = 0,
    ) -> None:
        # Bu metod eski main.py ile geriye dönük uyumluluk için tutulur.
        # Asıl ayrıntılı kayıt create_signal() ile yapılır.
        return None

    def create_signal(self, r: dict) -> int:
        now = time.time()

        with self._write() as conn:
            cur = conn.execute(
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
                    phase,
                    entry_quality,
                    priority,
                    streak,
                    trap,
                    d30,
                    d90,
                    trade_1m,
                    trade_5m,
                    market_momentum,
                    vr,
                    vr5,
                    bp,
                    rv,
                    ad,
                    dist,
                    impulse,
                    breakout,
                    closed_breakout,
                    ema,
                    macd,
                    squeeze,
                    higher_low,
                    group_count,
                    chg
                )
                VALUES(
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    r["symbol"],
                    now,
                    float(r.get("price", 0)),
                    float(r.get("score", 0)),
                    float(r.get("setup", 0)),
                    float(r.get("confirmation", 0)),
                    float(r.get("penalty", 0)),
                    r.get("status", "BUY"),
                    r.get("phase", "CONFIRMED"),
                    float(r.get("entry_quality", 0)),
                    float(r.get("priority", 0)),
                    int(r.get("streak", 0)),
                    1 if r.get("trap") else 0,
                    r.get("d30"),
                    r.get("d90"),
                    r.get("trades_1m"),
                    r.get("trades_5m"),
                    r.get("market_momentum"),
                    r.get("vr"),
                    r.get("vr5"),
                    r.get("bp"),
                    r.get("rv"),
                    r.get("ad"),
                    r.get("dist"),
                    r.get("impulse"),
                    1 if r.get("breakout") else 0,
                    1 if r.get("closed_breakout") else 0,
                    1 if r.get("ema") else 0,
                    1 if r.get("macd") else 0,
                    1 if r.get("squeeze") else 0,
                    1 if r.get("hl") else 0,
                    int(r.get("group_count", 0)),
                    r.get("chg"),
                ),
            )

            return int(cur.lastrowid)

    def signal_history(self, symbol: str) -> dict:
        conn = self._conn()

        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM signals
            WHERE symbol=?
            """,
            (symbol,),
        ).fetchone()

        count = int(row["count"] or 0)

        price_row = conn.execute(
            """
            SELECT price, ts
            FROM signals
            WHERE symbol=?
            ORDER BY ts DESC, id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()

        if price_row is None:
            return {
                "count": 0,
                "last_price": None,
                "last_ts": None,
            }

        return {
            "count": count,
            "last_price": (
                float(price_row["price"])
                if price_row["price"] is not None
                else None
            ),
            "last_ts": (
                float(price_row["ts"])
                if price_row["ts"] is not None
                else None
            ),
        }

    def update_outcomes(
        self,
        price_map: dict[str, float],
        outcome_window: int,
    ) -> int:
        now = time.time()
        changed = 0

        conn = self._conn()

        rows = conn.execute(
            """
            SELECT
                id,
                symbol,
                ts,
                price,
                c5,
                c15,
                max_pct,
                min_pct
            FROM signals
            WHERE price > 0
              AND (
                    c15 IS NULL
                    OR max_pct IS NULL
                    OR min_pct IS NULL
                  )
            ORDER BY id
            """
        ).fetchall()

        with self._write() as db:
            for row in rows:
                symbol = row["symbol"]
                current = price_map.get(symbol)

                if current is None or current <= 0:
                    continue

                age = now - float(row["ts"])
                entry = float(row["price"])

                move = (current - entry) / entry * 100

                max_pct = row["max_pct"]
                min_pct = row["min_pct"]

                if max_pct is None or move > float(max_pct):
                    max_pct = move

                if min_pct is None or move < float(min_pct):
                    min_pct = move

                c5 = row["c5"]
                c15 = row["c15"]

                # We cannot reconstruct the exact historical 5m/15m
                # high/low from ticker snapshots. The current move is used
                # as a conservative rolling observation until the window
                # completes.
                if age >= 300 and c5 is None:
                    c5 = move

                if age >= outcome_window and c15 is None:
                    c15 = move

                db.execute(
                    """
                    UPDATE signals
                    SET c5=?,
                        c15=?,
                        max_pct=?,
                        min_pct=?
                    WHERE id=?
                    """,
                    (
                        c5,
                        c15,
                        max_pct,
                        min_pct,
                        row["id"],
                    ),
                )

                changed += 1

        return changed

    def performance_summary(self) -> list[tuple]:
        conn = self._conn()

        rows = conn.execute(
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

        return [tuple(row) for row in rows]

    def cleanup_old_signals(self) -> int:
        cutoff = time.time() - (
            self.retention_days * 86400
        )

        with self._write() as conn:
            cur = conn.execute(
                """
                DELETE FROM signals
                WHERE ts < ?
                """,
                (cutoff,),
            )

            return int(cur.rowcount)

    def close(self) -> None:
        conn = getattr(
            self._local,
            "conn",
            None,
        )

        if conn is not None:
            conn.close()
            self._local.conn = None
