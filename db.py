import sqlite3
import time


class DB:

    def __init__(
        self,
        path,
        retention_days=30,
    ):
        self.path = path
        self.retention_days = retention_days
        self._init()

    def _connect(self):

        return sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
        )

    def _init(self):

        db = self._connect()

        try:

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    status TEXT,
                    score REAL,
                    streak INTEGER,
                    priority REAL,
                    entry_quality REAL,
                    price REAL,
                    stop_loss REAL,
                    sent INTEGER DEFAULT 0
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_signals_symbol_time
                ON signals(symbol, created_at)
                """
            )

            db.commit()

        finally:

            db.close()

    def create_signal(self, result):

        db = self._connect()

        try:

            db.execute(
                """
                INSERT INTO signals (
                    symbol,
                    created_at,
                    status,
                    score,
                    streak,
                    priority,
                    entry_quality,
                    price,
                    stop_loss,
                    sent
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.get(
                        "symbol",
                        "",
                    ),
                    time.time(),
                    result.get(
                        "status",
                        "",
                    ),
                    result.get(
                        "score",
                        0,
                    ),
                    result.get(
                        "streak",
                        1,
                    ),
                    result.get(
                        "priority",
                        0,
                    ),
                    result.get(
                        "entry_quality",
                        0,
                    ),
                    result.get(
                        "price",
                        0,
                    ),
                    result.get(
                        "stop_loss",
                        result.get(
                            "stop",
                            0,
                        ),
                    ),
                    1,
                ),
            )

            db.commit()

        finally:

            db.close()

    def get_last_signal(self, symbol):

        db = self._connect()

        try:

            row = db.execute(
                """
                SELECT
                    id,
                    symbol,
                    created_at,
                    status,
                    score,
                    streak,
                    priority,
                    entry_quality,
                    price,
                    stop_loss,
                    sent
                FROM signals
                WHERE symbol = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    symbol,
                ),
            ).fetchone()

            if not row:
                return None

            keys = [
                "id",
                "symbol",
                "created_at",
                "status",
                "score",
                "streak",
                "priority",
                "entry_quality",
                "price",
                "stop_loss",
                "sent",
            ]

            return dict(
                zip(
                    keys,
                    row,
                )
            )

        finally:

            db.close()

    def can_send(
        self,
        symbol,
        level,
        cooldown,
    ):

        last = self.get_last_signal(
            symbol
        )

        if not last:
            return True

        now = time.time()

        age = (
            now
            - float(
                last.get(
                    "created_at",
                    0,
                )
            )
        )

        if age >= cooldown:
            return True

        old_level = last.get(
            "status",
            "",
        )

        if level == "VERY":
            return True

        if old_level == "VERY":
            return False

        if level == "BUY" and old_level == "ONCU":
            return True

        return False

    def put(
        self,
        symbol,
        score=0,
        level="",
        stage="",
        sent=False,
        streak=1,
        trap=False,
        priority=0,
    ):

        db = self._connect()

        try:

            db.execute(
                """
                UPDATE signals
                SET
                    score = ?,
                    status = ?,
                    streak = ?,
                    priority = ?,
                    sent = ?
                WHERE id = (
                    SELECT id
                    FROM signals
                    WHERE symbol = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                )
                """,
                (
                    score,
                    level or stage,
                    streak,
                    priority,
                    1 if sent else 0,
                    symbol,
                ),
            )

            db.commit()

        finally:

            db.close()

    def update_outcomes(
        self,
        price_map,
        outcome_window,
    ):

        return 0

    def cleanup_old_signals(self):

        limit = (
            time.time()
            - (
                self.retention_days
                * 86400
            )
        )

        db = self._connect()

        try:

            cur = db.execute(
                """
                DELETE FROM signals
                WHERE created_at < ?
                """,
                (
                    limit,
                ),
            )

            db.commit()

            return cur.rowcount

        finally:

            db.close()

    def performance_summary(self):

        db = self._connect()

        try:

            rows = db.execute(
                """
                SELECT
                    symbol,
                    status,
                    score,
                    streak,
                    created_at
                FROM signals
                ORDER BY created_at DESC
                """
            ).fetchall()

            return rows

        finally:

            db.close()
