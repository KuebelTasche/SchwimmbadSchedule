"""SQLite-Datenbank: speichert Kursblöcke und deren Einzeltermine.

Wichtig: Einmal gespeicherte Kurse werden NIE gelöscht. Verschwindet ein Kurs
aus dem Portal (z. B. weil er ausgebucht ist oder schon läuft), bleibt er hier
mit allen Terminen erhalten, bis sein letzter Termin vorbei ist.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path(os.environ.get("NETTEBAD_DB", Path(__file__).parent / "data" / "nettebad.sqlite"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    block_id        INTEGER PRIMARY KEY,   -- ID aus dem Buchungsportal
    course_name     TEXT NOT NULL,
    tab             TEXT,                  -- Portal-Kategorie, z. B. "AquaFitness"
    location        TEXT,                  -- Filiale, z. B. "Nettebad"
    date_from       TEXT,                  -- ISO-Datum erster Termin
    date_to         TEXT,                  -- ISO-Datum letzter Termin
    n_sessions      INTEGER,
    weekdays        TEXT,                  -- z. B. "Dienstag, Donnerstag"
    times           TEXT,                  -- z. B. "15:25, 15:25"
    url             TEXT,
    free_places     INTEGER,               -- freie Plätze laut Portal
    booking_status  TEXT,                  -- buchbar | ausgebucht | gestartet | noch nicht buchbar
    booking_from    TEXT,                  -- ab wann buchbar (falls noch nicht buchbar)
    first_seen      TEXT NOT NULL,         -- wann wir ihn zum ersten Mal gesehen haben
    last_seen       TEXT NOT NULL,         -- wann er zuletzt im Portal stand
    details_fetched TEXT                   -- wann die Einzeltermine zuletzt geladen wurden
);

CREATE TABLE IF NOT EXISTS sessions (
    block_id   INTEGER NOT NULL REFERENCES blocks(block_id),
    nr         INTEGER,
    date       TEXT NOT NULL,              -- ISO-Datum
    start      TEXT NOT NULL,              -- "15:25"
    end        TEXT NOT NULL,              -- "16:10"
    status     TEXT DEFAULT '',            -- "" | "vorbei" | "abgesagt" | "geschätzt"
    PRIMARY KEY (block_id, date, start)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


# Server in der Cloud (Streamlit, GitHub Actions) laufen in UTC – wir rechnen immer in deutscher Zeit.
TZ = ZoneInfo("Europe/Berlin")


def now() -> datetime:
    """Aktuelle Uhrzeit in Deutschland (ohne Zeitzonen-Anhang, passend zu den Kurszeiten)."""
    return datetime.now(TZ).replace(tzinfo=None)


def today() -> date:
    return now().date()


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


@contextmanager
def connect(path: Path | None = None):
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _migrate(con: sqlite3.Connection) -> None:
    """Ältere Datenbanken um neue Spalten ergänzen."""
    spalten = {r["name"] for r in con.execute("PRAGMA table_info(sessions)")}
    if "status" not in spalten:
        con.execute("ALTER TABLE sessions ADD COLUMN status TEXT DEFAULT ''")
    spalten = {r["name"] for r in con.execute("PRAGMA table_info(blocks)")}
    for name, typ in [("free_places", "INTEGER"), ("booking_status", "TEXT"), ("booking_from", "TEXT")]:
        if name not in spalten:
            con.execute(f"ALTER TABLE blocks ADD COLUMN {name} {typ}")


def upsert_block(con: sqlite3.Connection, b: dict) -> bool:
    """Legt einen Block an oder aktualisiert ihn. Gibt True zurück, wenn er neu ist."""
    ts = now_iso()
    exists = con.execute("SELECT 1 FROM blocks WHERE block_id=?", (b["block_id"],)).fetchone()
    if exists:
        con.execute(
            """UPDATE blocks SET course_name=?, tab=?, location=?, date_from=?, date_to=?,
                   n_sessions=?, weekdays=?, times=?, url=?, last_seen=?,
                   free_places=?, booking_status=?, booking_from=?
               WHERE block_id=?""",
            (b["course_name"], b["tab"], b["location"], b["date_from"], b["date_to"],
             b["n_sessions"], b["weekdays"], b["times"], b["url"], ts,
             b.get("free_places"), b.get("booking_status"), b.get("booking_from"), b["block_id"]),
        )
        return False
    con.execute(
        """INSERT INTO blocks (block_id, course_name, tab, location, date_from, date_to,
               n_sessions, weekdays, times, url, first_seen, last_seen,
               free_places, booking_status, booking_from)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (b["block_id"], b["course_name"], b["tab"], b["location"], b["date_from"], b["date_to"],
         b["n_sessions"], b["weekdays"], b["times"], b["url"], ts, ts,
         b.get("free_places"), b.get("booking_status"), b.get("booking_from")),
    )
    return True


def replace_sessions(con: sqlite3.Connection, block_id: int, sessions: list[dict]) -> None:
    """Ersetzt die Einzeltermine eines Blocks durch die frisch geladenen."""
    if not sessions:
        return  # lieber alte Daten behalten als alles zu löschen
    con.execute("DELETE FROM sessions WHERE block_id=?", (block_id,))
    con.executemany(
        "INSERT OR REPLACE INTO sessions (block_id, nr, date, start, end, status) VALUES (?,?,?,?,?,?)",
        [(block_id, s["nr"], s["date"], s["start"], s["end"], s.get("status", ""))
         for s in sessions],
    )
    con.execute("UPDATE blocks SET details_fetched=? WHERE block_id=?", (now_iso(), block_id))


def blocks_needing_details(con: sqlite3.Connection, today: str) -> list[int]:
    """Blöcke, deren Termine wir (neu) laden sollten: alle, die noch nicht vorbei sind.

    Vergangene Kurse brauchen wir nicht – das Portal löscht deren Detailseiten ohnehin (404).
    """
    rows = con.execute(
        """SELECT block_id FROM blocks
           WHERE date_to IS NULL OR date_to >= ?
           ORDER BY block_id""",
        (today,),
    ).fetchall()
    return [r["block_id"] for r in rows]


def get_block(con: sqlite3.Connection, block_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM blocks WHERE block_id=?", (block_id,)).fetchone()


def get_meta(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
