import sqlite3
from datetime import datetime, timezone


class Database:
    def __init__(self, path):
        self.path = path

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER NOT NULL,
                target_type TEXT NOT NULL,
                target_value TEXT NOT NULL,
                reason TEXT NOT NULL,
                evidence TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                moderated_by INTEGER,
                moderated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO settings(key, value) VALUES ('paid_mode', '0');
            INSERT OR IGNORE INTO settings(key, value) VALUES ('check_price', '0');
            """)

    def add_admin(self, user_id):
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (user_id,))

    def is_admin(self, user_id):
        with self.connect() as c:
            return c.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone() is not None

    def admin_ids(self):
        with self.connect() as c:
            return [r["user_id"] for r in c.execute("SELECT user_id FROM admins")]

    def create_report(self, reporter_id, target_type, target_value, reason, evidence):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as c:
            cur = c.execute(
                """INSERT INTO reports
                (reporter_id,target_type,target_value,reason,evidence,status,created_at)
                VALUES (?,?,?,?,?,'pending',?)""",
                (reporter_id, target_type, target_value, reason, evidence, now)
            )
            return cur.lastrowid

    def public_lookup(self, target_type, target_value):
        with self.connect() as c:
            rows = c.execute(
                """SELECT id, category, reason, created_at FROM
                (SELECT id, 'Жалоба, прошедшая модерацию' AS category, reason, created_at
                 FROM reports WHERE target_type=? AND target_value=? AND status='approved')
                ORDER BY created_at DESC LIMIT 10""",
                (target_type, target_value)
            ).fetchall()
            return [dict(r) for r in rows]

    def pending_reports(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM reports WHERE status='pending' ORDER BY created_at"
            ).fetchall()]

    def next_pending(self):
        with self.connect() as c:
            r = c.execute(
                "SELECT * FROM reports WHERE status='pending' ORDER BY created_at LIMIT 1"
            ).fetchone()
            return dict(r) if r else None

    def moderate_report(self, report_id, status, admin_id):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as c:
            cur = c.execute(
                """UPDATE reports SET status=?, moderated_by=?, moderated_at=?
                WHERE id=? AND status='pending'""",
                (status, admin_id, now, report_id)
            )
            return cur.rowcount == 1

    def stats(self):
        with self.connect() as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM reports GROUP BY status").fetchall()
            counts = {r["status"]: r["n"] for r in rows}
            return {
                "total": sum(counts.values()),
                "pending": counts.get("pending", 0),
                "approved": counts.get("approved", 0),
                "rejected": counts.get("rejected", 0),
            }

    def get_pricing(self):
        with self.connect() as c:
            values = {r["key"]: r["value"] for r in c.execute(
                "SELECT key,value FROM settings WHERE key IN ('paid_mode','check_price')"
            )}
            return values.get("paid_mode", "0") == "1", values.get("check_price", "0")
