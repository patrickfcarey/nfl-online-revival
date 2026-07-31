"""Durable state, in SQLite.

Column names are the protocol's own key strings, recovered from the client
binary -- see ``docs/backend-data-model.md`` for where each one lives. Keeping
the names identical means a handler can hand a row almost straight to the wire
without a translation layer to get out of step.

Only durable state lives here. Sessions, presence, room occupancy and paging
cursors are per-connection and stay in memory; writing them would mean
reconciling them after a crash for no benefit, since the client re-establishes
all of it on reconnect.

Passwords are stored exactly as the client sends them. It encrypts ``PASS``
under the negotiated session key and prefixes ``~``, and that cipher is not
reversed, so the server never sees plaintext. Authentication compares what is
presented against what was stored. That is weaker than a password hash and it
is worth being explicit about: this reproduces a 2003 protocol on a private
LAN, and the wire format is not ours to change.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Iterable, List, Optional, Sequence

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS account(
  NAME   TEXT PRIMARY KEY,
  PASS   TEXT,
  MAIL   TEXT,
  PMAIL  TEXT,
  BORN   TEXT,
  GEND   TEXT,
  SPAM   TEXT,
  TOS    INTEGER DEFAULT 0,
  CPAT   INTEGER DEFAULT 0,
  ALTS   INTEGER DEFAULT 4,
  OPTS   TEXT DEFAULT '',
  CDEV   TEXT DEFAULT '',
  AGE    INTEGER,
  CHNG   TEXT,
  created INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS persona(
  PERS   TEXT PRIMARY KEY,
  NAME   TEXT NOT NULL REFERENCES account(NAME) ON DELETE CASCADE,
  created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS persona_by_account ON persona(NAME);

CREATE TABLE IF NOT EXISTS rank_stat(
  PERS TEXT NOT NULL, CHAN TEXT NOT NULL, STAT TEXT NOT NULL,
  WEEK INTEGER NOT NULL DEFAULT 0,
  RANK INTEGER, SKIL INTEGER, INFO TEXT,
  PRIMARY KEY(PERS, CHAN, STAT, WEEK));

CREATE TABLE IF NOT EXISTS game(
  GAMEID INTEGER PRIMARY KEY AUTOINCREMENT,
  WHEN_ INTEGER, REPT INTEGER, AUTH TEXT, DISC INTEGER,
  QLEN INTEGER, DSYNC INTEGER, TOURNEYID INTEGER, ROUND INTEGER);

CREATE TABLE IF NOT EXISTS game_side(
  GAMEID INTEGER NOT NULL REFERENCES game(GAMEID) ON DELETE CASCADE,
  SIDE INTEGER NOT NULL,
  NAME TEXT, TEAM INTEGER, WEIGHT INTEGER,
  SCORE INTEGER, DSCORE INTEGER, INTS INTEGER, SACKS INTEGER, CHT INTEGER,
  RUSHATT INTEGER, RUSHYDS INTEGER, PASSATT INTEGER, PASSYDS INTEGER,
  TURNOVERS INTEGER,
  PRIMARY KEY(GAMEID, SIDE));

CREATE TABLE IF NOT EXISTS tourney(
  TID INTEGER PRIMARY KEY AUTOINCREMENT,
  TNAME TEXT NOT NULL UNIQUE,
  PASS TEXT, TEXT_ TEXT, SIZE INTEGER, ROUND INTEGER DEFAULT 0,
  START INTEGER, LIFE INTEGER, INACT INTEGER, UNIQ INTEGER,
  UFLAG INTEGER, SFLAG INTEGER, UPARM TEXT, ROLLOVER INTEGER, MOD INTEGER,
  created INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS tourney_member(
  TID INTEGER NOT NULL REFERENCES tourney(TID) ON DELETE CASCADE,
  PERS TEXT NOT NULL, TEAM INTEGER, RANK INTEGER, UPARM TEXT, IDX INTEGER,
  PRIMARY KEY(TID, PERS));

CREATE TABLE IF NOT EXISTS tourney_node(
  TID INTEGER NOT NULL REFERENCES tourney(TID) ON DELETE CASCADE,
  RND INTEGER NOT NULL, IDX INTEGER NOT NULL,
  U0 TEXT, U1 TEXT, W TEXT, G INTEGER, R INTEGER, I INTEGER, ST2 INTEGER,
  PRIMARY KEY(TID, RND, IDX));

CREATE TABLE IF NOT EXISTS buddy(
  USER TEXT NOT NULL, LIST TEXT NOT NULL, LRSC TEXT, GROUP_ TEXT, ID INTEGER,
  PRIMARY KEY(USER, LIST));

CREATE TABLE IF NOT EXISTS message(
  ID INTEGER PRIMARY KEY AUTOINCREMENT,
  USER TEXT NOT NULL, FUSR TEXT, SUBJ TEXT, BODY TEXT,
  TIME INTEGER, SIZE INTEGER);
CREATE INDEX IF NOT EXISTS message_by_user ON message(USER);

CREATE TABLE IF NOT EXISTS room(
  NAME TEXT PRIMARY KEY, DESC TEXT, PASS TEXT, MAX INTEGER, CHAN TEXT);
"""

#: The client shows a persona picker with four slots.
MAX_PERSONAS = 4
#: Persona names are read through a 13-byte buffer, so twelve characters fit.
MAX_PERSONA_LEN = 12


class StoreError(RuntimeError):
    """The store could not be opened or migrated."""


class Store:
    """Durable state. Safe to share across connection threads.

    One connection behind a lock, rather than a connection per thread: at this
    scale contention is irrelevant, and a single writer removes any question
    about SQLite threading modes.
    """

    def __init__(self, path: str = "backend.db") -> None:
        self.path = path
        self._lock = threading.RLock()
        try:
            self._db = sqlite3.connect(path, check_same_thread=False)
        except sqlite3.Error as exc:
            raise StoreError("cannot open %s: %s" % (path, exc))
        self._db.row_factory = sqlite3.Row
        with self._lock:
            # WAL so a reader never blocks the writer; foreign keys so the
            # persona/account cascade is enforced by the database rather than
            # by every caller remembering to.
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(SCHEMA)
            self._db.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)",
                (str(SCHEMA_VERSION),))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- accounts ------------------------------------------------------

    def account(self, name: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM account WHERE NAME = ?", (name,)).fetchone()

    def account_exists(self, name: str) -> bool:
        return self.account(name) is not None

    def create_account(self, name: str, **fields) -> None:
        """Insert an account. Raises sqlite3.IntegrityError if the name is taken.

        The caller turns that into the client's ``dupl`` tag; the uniqueness
        itself is the database's job so two simultaneous registrations cannot
        both succeed.
        """
        columns = ["NAME", "created"]
        values: List[object] = [name, int(time.time())]
        for key, value in fields.items():
            if value is None:
                continue
            columns.append(key)
            values.append(value)
        sql = "INSERT INTO account(%s) VALUES(%s)" % (
            ", ".join(columns), ", ".join("?" * len(columns)))
        with self._lock:
            self._db.execute(sql, values)
            self._db.commit()

    def update_account(self, name: str, **fields) -> None:
        if not fields:
            return
        assignments = ", ".join("%s = ?" % key for key in fields)
        with self._lock:
            self._db.execute("UPDATE account SET %s WHERE NAME = ?" % assignments,
                             list(fields.values()) + [name])
            self._db.commit()

    # -- personas ------------------------------------------------------

    def personas(self, account_name: str) -> List[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT PERS FROM persona WHERE NAME = ? ORDER BY created",
                (account_name,)).fetchall()
        return [row["PERS"] for row in rows]

    def persona_owner(self, persona: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute(
                "SELECT NAME FROM persona WHERE PERS = ?", (persona,)).fetchone()
        return row["NAME"] if row else None

    def create_persona(self, account_name: str, persona: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO persona(PERS, NAME, created) VALUES(?, ?, ?)",
                (persona, account_name, int(time.time())))
            self._db.commit()

    def delete_persona(self, account_name: str, persona: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM persona WHERE PERS = ? AND NAME = ?",
                (persona, account_name))
            self._db.commit()
        return cursor.rowcount > 0

    # -- rooms ---------------------------------------------------------

    def rooms(self) -> List[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM room ORDER BY NAME").fetchall()

    def ensure_room(self, name: str, desc: str = "", max_users: int = 32,
                    chan: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO room(NAME, DESC, PASS, MAX, CHAN) "
                "VALUES(?, ?, '', ?, ?)", (name, desc, max_users, chan))
            self._db.commit()

    # -- diagnostics ---------------------------------------------------

    def counts(self) -> dict:
        """Row counts per table, for a one-line health report."""
        out = {}
        with self._lock:
            for table in ("account", "persona", "room", "game", "tourney",
                          "buddy", "message"):
                out[table] = self._db.execute(
                    "SELECT COUNT(*) AS n FROM %s" % table).fetchone()["n"]
        return out
