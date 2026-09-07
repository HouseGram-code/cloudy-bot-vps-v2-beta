import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .errors import CloudyError
from .models import VPS, utc_now

class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS vps (
                    id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    owner_id INTEGER NOT NULL,
                    container_id TEXT,
                    container_name TEXT NOT NULL UNIQUE,
                    network_name TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    ram_bytes INTEGER NOT NULL,
                    cpus INTEGER NOT NULL,
                    disk_bytes INTEGER NOT NULL,
                    UNIQUE(guild_id, owner_id)
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at INTEGER NOT NULL,
                    owner_id INTEGER NOT NULL,
                    vps_id TEXT,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL
                );
            """)
        path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def row(row):
        return VPS(**dict(row)) if row else None

    def all(self):
        with self.connection() as db:
            return [self.row(row) for row in db.execute("SELECT * FROM vps ORDER BY created_at")]

    def get(self, vps_id):
        with self.connection() as db:
            return self.row(db.execute("SELECT * FROM vps WHERE id=?", (vps_id,)).fetchone())

    def for_owner(self, guild_id, owner_id):
        with self.connection() as db:
            return self.row(db.execute("SELECT * FROM vps WHERE guild_id=? AND owner_id=?", (guild_id, owner_id)).fetchone())

    def owned(self, vps_id, guild_id, owner_id):
        row = self.get(vps_id)
        if not row or row.guild_id != guild_id or row.owner_id != owner_id:
            raise CloudyError("This VPS does not belong to you.", "not_owner")
        return row

    def add(self, vps: VPS):
        values = vars(vps)
        try:
            with self.connection() as db:
                db.execute("INSERT INTO vps (" + ",".join(values) + ") VALUES (" + ",".join("?" for _ in values) + ")", tuple(values.values()))
        except sqlite3.IntegrityError as exc:
            raise CloudyError("You already have a VPS. Use $manage.", "already_exists") from exc

    def update(self, vps_id, **fields):
        if not fields or not set(fields) <= {"container_id", "state"}:
            raise ValueError("Unsupported update")
        with self.connection() as db:
            db.execute("UPDATE vps SET " + ",".join(f"{name}=?" for name in fields) + " WHERE id=?", (*fields.values(), vps_id))

    def delete(self, vps_id):
        with self.connection() as db:
            db.execute("DELETE FROM vps WHERE id=?", (vps_id,))

    def audit(self, owner_id, vps_id, action, outcome="ok"):
        with self.connection() as db:
            db.execute("INSERT INTO audit (at,owner_id,vps_id,action,outcome) VALUES (?,?,?,?,?)", (utc_now(), owner_id, vps_id, action, outcome))
            db.execute("DELETE FROM audit WHERE at < ?", (utc_now() - 90 * 86400,))
