from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class DB:
    def __init__(self, path="balina.db"):
        self.path = str(Path(path))
        self.lock = threading.Lock()
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self.lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS streaks (
                    symbol TEXT PRIMARY KEY,
                    streak INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    movement_no INTEGER NOT NULL,
                    phase TEXT,
                    price REAL,
                    score REAL,
                    created_at INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signal_history_symbol
                ON signal_history(symbol)
            """)
            conn.commit()

    def update_streak(self, symbol, qualified, trap=False):
        now = int(time.time())

        with self.lock, self._connect() as conn:
            row = conn.execute(
                "SELECT streak FROM streaks WHERE symbol=?",
                (symbol,)
            ).fetchone()

            current = int(row["streak"]) if row else 0

            if trap:
                current = 0
            elif qualified:
                current += 1
            else:
                current = 0

            conn.execute("""
                INSERT INTO streaks(symbol, streak, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    streak=excluded.streak,
                    updated_at=excluded.updated_at
            """, (symbol, current, now))
            conn.commit()

        return current

    def movement_count(self, symbol):
        with self.lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT movement_no) AS n FROM signal_history WHERE symbol=?",
                (symbol,)
            ).fetchone()
            return int(row["n"]) if row else 0

    def previous_signals(self, symbol):
        return self.movement_count(symbol)

    def current_movement(self, symbol):
        return self.movement_count(symbol) + 1

    def add_signal(self, symbol, phase=None, price=None, score=None):
        now = int(time.time())

        with self.lock, self._connect() as conn:
            row = conn.execute(
                "SELECT movement_no, phase, price FROM signal_history "
                "WHERE symbol=? ORDER BY id DESC LIMIT 1",
                (symbol,)
            ).fetchone()

            if row and row["phase"] == phase:
                return int(row["movement_no"])

            movement_no = int(row["movement_no"]) + 1 if row else 1

            conn.execute("""
                INSERT INTO signal_history
                (symbol, movement_no, phase, price, score, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                movement_no,
                phase,
                price,
                score,
                now
            ))
            conn.commit()

        return movement_no

    def signal_info(self, symbol):
        previous = self.movement_count(symbol)

        return {
            "previous_signals": previous,
            "movement_no": previous + 1
        }

    def get_signal_history(self, symbol, limit=20):
        with self.lock, self._connect() as conn:
            rows = conn.execute("""
                SELECT movement_no, phase, price, score, created_at
                FROM signal_history
                WHERE symbol=?
                ORDER BY id DESC
                LIMIT ?
            """, (symbol, int(limit))).fetchall()

        return [dict(row) for row in rows]

    def close(self):
        pass
        
