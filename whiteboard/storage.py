"""SQLite persistence. One connection per operation; safe for threaded server."""
import sqlite3
import json
from pathlib import Path
from contextlib import contextmanager

class Store:
    def __init__(self, path="whiteboard.sqlite3"):
        self.path = str(Path(path))
        with self._db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS rooms(
              code TEXT PRIMARY KEY, name TEXT NOT NULL, salt BLOB NOT NULL,
              password_hash BLOB NOT NULL, owner_token_hash BLOB NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events(
              room TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(room,seq)
            );
            CREATE TABLE IF NOT EXISTS chat(
              id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL,
              username TEXT NOT NULL, text TEXT NOT NULL, ts TEXT NOT NULL
            );
            """)
    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    def create_room(self, code, name, salt, password_hash, owner_hash, created):
        with self._db() as db:
            db.execute("INSERT INTO rooms VALUES(?,?,?,?,?,?)",
                       (code,name,salt,password_hash,owner_hash,created))
    def room(self, code):
        with self._db() as db:
            row = db.execute("SELECT * FROM rooms WHERE code=?", (code,)).fetchone()
            return dict(row) if row else None
    def append_event(self, room, seq, payload):
        with self._db() as db:
            db.execute("INSERT INTO events VALUES(?,?,?)",
                       (room,seq,json.dumps(payload,separators=(",",":"))))
    def events(self, room):
        with self._db() as db:
            rows = db.execute("SELECT payload FROM events WHERE room=? ORDER BY seq", (room,)).fetchall()
            return [json.loads(r["payload"]) for r in rows]
    def add_chat(self, room, username, text, ts):
        with self._db() as db:
            cur = db.execute("INSERT INTO chat(room,username,text,ts) VALUES(?,?,?,?)",
                             (room,username,text,ts))
            return cur.lastrowid
    def chats(self, room, limit=200):
        with self._db() as db:
            rows = db.execute("SELECT id,username,text,ts FROM chat WHERE room=? ORDER BY id DESC LIMIT ?",
                              (room,limit)).fetchall()
            return [dict(r) for r in reversed(rows)]
