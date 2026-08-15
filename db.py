from __future__ import annotations

import sqlite3
import time
from threading import Lock


class DB:
    """SQLite tabanlı durum ve sinyal geçmişi.

    Mevcut bot API'siyle uyumludur:
    - update_streak()
    - can_send()
    - put()
    - create_signal()
    - update_outcomes()
    - performance_summary()
    - cleanup_old_signals()

    Aynı coin için sinyal geçmişi ayrıca signals tablosunda tutulur.
    Böylece Telegram tarafında Sinyal #1, #2, #3 bilgisi üretilebilir.
    """

    def __init__(self, path: str, retention_days: int = 30):
        self.path = path
        self.retention_days = retention_days
        self._lock = Lock()

        with self._connect() as con:
            self._init_schema(con)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
        )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    @staticmethod
    def _init_schema(con: sqlite3.Connection) -> None:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                level TEXT NOT NULL,
                sent_at REAL,
                streak INTEGER NOT NULL DEFAULT 0,
                trap INTEGER NOT NULL DEFAULT 0,
                priority REAL NOT NULL DEFAULT 0,
                entry_quality REAL NOT NULL DEFAULT 0,

                price REAL,
                max_pct REAL,
                min_pct REAL,
                pct_15m REAL,

                created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
                outcome_checked_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_signals_symbol
                ON signals(symbol);

            CREATE INDEX IF NOT EXISTS idx_signals_sent_at
                ON signals(sent_at);

            CREATE INDEX IF NOT EXISTS idx_signals_created_at
                ON signals(created_at);

            CREATE TABLE IF NOT EXISTS streaks (
                symbol TEXT PRIMARY KEY,
                streak INTEGER NOT NULL DEFAULT 0,
                last_ok REAL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS send_state (
                symbol TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                sent_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )

        # Eski DB'lerde sonradan eklenen sütunlar varsa eksik olanları tamamla.
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(signals)").fetchall()
        }

        additions = {
            "level": "TEXT NOT NULL DEFAULT 'BUY'",
            "streak": "INTEGER NOT NULL DEFAULT 0",
            "trap": "INTEGER NOT NULL DEFAULT 0",
            "priority": "REAL NOT NULL DEFAULT 0",
            "entry_quality": "REAL NOT NULL DEFAULT 0",
            "price": "REAL",
            "max_pct": "REAL",
            "min_pct": "REAL",
            "pct_15m": "REAL",
            "created_at": "REAL",
            "outcome_checked_at": "REAL",
        }

        for name, definition in additions.items():
            if name not in columns:
                con.execute(
                    f"ALTER TABLE signals ADD COLUMN {name} {definition}"
                )

        con.commit()

    def update_streak(
        self,
        symbol: str,
        qualified: bool,
        trap: bool = False,
    ) -> int:
        now = time.time()

        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT streak, last_ok FROM streaks WHERE symbol = ?",
                (symbol,),
            ).fetchone()

            current = int(row["streak"]) if row else 0
            last_ok = float(row["last_ok"]) if row and row["last_ok"] else 0.0

            # Sinyal artık streak tarafından engellenmiyor.
            # Buradaki sayaç sadece hareketin ardışıklığını gösteriyor.
            if trap or not qualified:
                streak = 0
                last_ok_value = None
            else:
                streak = current + 1
                last_ok_value = now

            con.execute(
                """
                INSERT INTO streaks(symbol, streak, last_ok, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    streak = excluded.streak,
                    last_ok = excluded.last_ok,
                    updated_at = excluded.updated_at
                """,
                (symbol, streak, last_ok_value, now),
            )
            con.commit()

            return streak

    def signal_count(self, symbol: str) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM signals WHERE symbol = ?",
                (symbol,),
            ).fetchone()
            return int(row["n"]) if row else 0

    def last_signal(self, symbol: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT *
                FROM signals
                WHERE symbol = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()

            return dict(row) if row else None

    def can_send(
        self,
        symbol: str,
        status: str,
        cooldown: int,
    ) -> bool:
        now = time.time()

        with self._connect() as con:
            row = con.execute(
                """
                SELECT sent_at, status
                FROM send_state
                WHERE symbol = ?
                """,
                (symbol,),
            ).fetchone()

            if not row:
                return True

            sent_at = float(row["sent_at"] or 0)
            elapsed = now - sent_at

            if elapsed >= cooldown:
                return True

            # Aynı cooldown içinde daha güçlü seviyeye geçişe izin ver.
            strength = {
                "EARLY": 1,
                "BUY": 2,
                "VERY": 3,
            }
            old_strength = strength.get(row["status"], 0)
            new_strength = strength.get(status, 0)

            return new_strength > old_strength

    def put(
        self,
        symbol: str,
        score: float,
        status: str,
        level: str,
        sent: float | None = None,
        streak: int = 0,
        trap: bool = False,
        priority: float = 0,
        entry_quality: float = 0,
    ) -> None:
        sent_at = float(sent if sent is not None else time.time())
        now = time.time()

        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO send_state(symbol, status, sent_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    status = excluded.status,
                    sent_at = excluded.sent_at,
                    updated_at = excluded.updated_at
                """,
                (symbol, status, sent_at, now),
            )

            # Gönderilen sinyalin temel kaydını burada da tutuyoruz.
            con.execute(
                """
                INSERT INTO signals(
                    symbol, score, status, level, sent_at,
                    streak, trap, priority, entry_quality, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    score,
                    status,
                    level,
                    sent_at,
                    streak,
                    int(bool(trap)),
                    priority,
                    entry_quality,
                    now,
                ),
            )
            con.commit()

    def create_signal(self, result: dict) -> int:
        """Analiz sonucunu ayrıntılı sinyal kaydı olarak saklar.

        Aynı sinyal put() ile zaten temel olarak kaydedilmişse yeni bir
        ayrıntılı satır açmak yerine son kaydı doldurur.
        """
        now = time.time()
        symbol = result["symbol"]

        with self._lock, self._connect() as con:
            row = con.execute(
                """
                SELECT id
                FROM signals
                WHERE symbol = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()

            if row:
                signal_id = int(row["id"])
                con.execute(
                    """
                    UPDATE signals
                    SET price = ?,
                        priority = ?,
                        entry_quality = ?,
                        streak = ?,
                        trap = ?,
                        level = ?,
                        updated_at = COALESCE(updated_at, ?)
                    WHERE id = ?
                    """.replace(
                        "updated_at = COALESCE(updated_at, ?),",
                        ""
                    ),
                    (
                        result.get("price"),
                        result.get("priority", 0),
                        result.get("entry_quality", 0),
                        result.get("streak", 0),
                        int(bool(result.get("trap"))),
                        result.get("status", "BUY"),
                        signal_id,
                    ),
                )
            else:
                cur = con.execute(
                    """
                    INSERT INTO signals(
                        symbol, score, status, level, sent_at,
                        streak, trap, priority, entry_quality,
                        price, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        result.get("score", 0),
                        result.get("status", "BUY"),
                        result.get("status", "BUY"),
                        now,
                        result.get("streak", 0),
                        int(bool(result.get("trap"))),
                        result.get("priority", 0),
                        result.get("entry_quality", 0),
                        result.get("price"),
                        now,
                    ),
                )
                signal_id = int(cur.lastrowid)

            con.commit()
            return signal_id

    def update_outcomes(
        self,
        price_map: dict[str, float],
        outcome_window: int,
    ) -> int:
        """Gönderilen sinyallerin max/min ve 15dk sonucunu günceller."""
        now = time.time()
        updated = 0

        with self._lock, self._connect() as con:
            rows = con.execute(
                """
                SELECT id, symbol, price, sent_at, max_pct, min_pct, pct_15m
                FROM signals
                WHERE sent_at IS NOT NULL
                  AND price IS NOT NULL
                  AND (
                      max_pct IS NULL
                      OR min_pct IS NULL
                      OR (pct_15m IS NULL AND sent_at <= ?)
                  )
                """,
                (now - outcome_window,),
            ).fetchall()

            for row in rows:
                symbol = row["symbol"]
                current = price_map.get(symbol)

                if current is None:
                    continue

                entry = float(row["price"])
                if entry <= 0:
                    continue

                change = (float(current) - entry) / entry * 100.0

                max_pct = row["max_pct"]
                min_pct = row["min_pct"]

                if max_pct is None or change > float(max_pct):
                    max_pct = change

                if min_pct is None or change < float(min_pct):
                    min_pct = change

                pct_15m = row["pct_15m"]
                sent_at = float(row["sent_at"])

                if pct_15m is None and now - sent_at >= outcome_window:
                    pct_15m = change

                con.execute(
                    """
                    UPDATE signals
                    SET max_pct = ?,
                        min_pct = ?,
                        pct_15m = ?,
                        outcome_checked_at = ?
                    WHERE id = ?
                    """,
                    (
                        max_pct,
                        min_pct,
                        pct_15m,
                        now,
                        row["id"],
                    ),
                )
                updated += 1

            con.commit()

        return updated

    def performance_summary(self) -> list[tuple]:
        """Performans modülünün beklediği kolon sırasını döndürür.

        [0] score
        [1] symbol
        [2] created/sent
        [3] max_pct
        [4] min_pct
        [5] sent_at
        [6] pct_15m
        [7] status
        [8] entry_quality
        """
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT
                    score,
                    symbol,
                    created_at,
                    COALESCE(max_pct, 0),
                    COALESCE(min_pct, 0),
                    sent_at,
                    pct_15m,
                    status,
                    entry_quality
                FROM signals
                ORDER BY id DESC
                """
            ).fetchall()

            return [tuple(row) for row in rows]

    def cleanup_old_signals(self) -> int:
        cutoff = time.time() - self.retention_days * 86400

        with self._lock, self._connect() as con:
            cur = con.execute(
                """
                DELETE FROM signals
                WHERE created_at IS NOT NULL
                  AND created_at < ?
                """,
                (cutoff,),
            )
            removed = cur.rowcount

            con.execute(
                """
                DELETE FROM send_state
                WHERE sent_at < ?
                """,
                (cutoff,),
            )

            con.execute(
                """
                DELETE FROM streaks
                WHERE updated_at < ?
                """,
                (cutoff,),
            )

            con.commit()
            return int(removed)
