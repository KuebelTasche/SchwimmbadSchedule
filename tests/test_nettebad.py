"""Tests ohne Internet – mit echten HTML-Ausschnitten aus dem Portal.

Aufruf:  python -m pytest -q
"""
from datetime import date, datetime, time
from pathlib import Path

import pytest

import belegung
import db
import portal

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


# --- Parser -----------------------------------------------------------------

def test_block_list():
    blocks, total = portal.parse_block_list(read("block_list.html"), "Schwimmschule")
    assert total == 40
    assert [b["block_id"] for b in blocks] == [107540, 107489, 104853, 106822]
    seepf = blocks[1]
    assert seepf["course_name"] == "Seepferdchen"
    assert seepf["date_from"] == "2026-09-08" and seepf["date_to"] == "2026-11-12"
    assert seepf["n_sessions"] == 16
    assert seepf["weekdays"] == "Dienstag, Donnerstag"
    assert seepf["times"] == "15:25, 15:25"
    assert seepf["location"] == "Nettebad"
    assert blocks[2]["n_sessions"] == 10          # "10 8 Rest-Termine buchbar"
    assert blocks[3]["location"] == "Moskaubad"


def test_block_details():
    s = portal.parse_block_details(read("block_details.html"))
    assert len(s) == 5                                 # zweite Tabelle wird ignoriert
    assert s[0] == {"nr": 1, "date": "2026-09-08", "start": "15:25", "end": "16:10", "status": "vorbei"}
    assert s[2]["date"] == "2026-09-24" and s[2]["status"] == "abgesagt"
    assert s[-1]["date"] == "2026-10-01" and s[-1]["status"] == ""


def test_oeffnungszeiten():
    oz = portal.parse_oeffnungszeiten(read("oeffnungszeiten.html"))
    assert "Di 16 - 19 Uhr" in oz["ninjacross"]
    assert oz["33m"].startswith("8 - 21 Uhr")


# --- Datenbank: Kurse bleiben erhalten -----------------------------------------

@pytest.fixture(autouse=True)
def ohne_manuelle_zuordnung(tmp_path, monkeypatch):
    """Tests sollen nicht von deiner becken_manuell.yaml abhängen."""
    monkeypatch.setattr(belegung, "MANUELL_PATH", tmp_path / "becken_manuell.yaml")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.sqlite")
    return tmp_path


def test_kurs_bleibt_gespeichert(tmp_db):
    blocks, _ = portal.parse_block_list(read("block_list.html"), "Schwimmschule")
    sessions = portal.parse_block_details(read("block_details.html"))
    with db.connect() as con:
        assert db.upsert_block(con, blocks[1]) is True
        assert db.upsert_block(con, blocks[1]) is False   # zweites Mal nicht mehr neu
        db.replace_sessions(con, 107489, sessions)
        db.replace_sessions(con, 107489, [])              # leere Antwort löscht nichts
        n = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert n == 5
    # Ein Lauf später steht der Kurs nicht mehr im Portal – Termine sind trotzdem da.
    # Der abgesagte Termin am 24.09. zählt NICHT als Belegung:
    evs = belegung.kurs_events(date(2026, 9, 20), date(2026, 10, 1), belegung.load_config())
    assert [e.start.date().isoformat() for e in evs] == ["2026-09-29", "2026-10-01"]
    assert [e.titel for e in evs] == ["Seepferdchen", "Seepferdchen"]
    assert evs[0].becken == "33m" and evs[0].kategorie == "Schwimmschule"


# --- Ninjacross, Ferien, freie Zeiten ------------------------------------------

@pytest.fixture
def cfg():
    return belegung.load_config()


def test_ninja_normalwoche(cfg):
    evs = belegung.ninja_events(date(2026, 9, 28), date(2026, 10, 4), cfg)  # Mo–So
    tage = {e.start.date(): (e.start.time(), e.ende.time()) for e in evs}
    assert date(2026, 9, 28) not in tage                          # Montag zu
    assert tage[date(2026, 9, 29)] == (time(16), time(19))        # Di
    assert tage[date(2026, 10, 2)] == (time(16), time(20))        # Fr
    assert tage[date(2026, 10, 3)] == (time(12), time(19))        # Sa (Feiertag)


def test_ninja_herbstferien(cfg):
    evs = belegung.ninja_events(date(2026, 10, 12), date(2026, 10, 13), cfg)
    assert len(evs) == 1                                          # Mo zu, Di offen
    assert evs[0].start == datetime(2026, 10, 13, 12)


def test_freie_zeiten(cfg):
    tag = date(2026, 9, 29)  # Dienstag, 33m offen 8–21
    evs = [
        belegung.Event("A", datetime(2026, 9, 29, 15, 25), datetime(2026, 9, 29, 16, 10), "33m", "x", "#000", "t"),
        belegung.Event("B", datetime(2026, 9, 29, 16, 0), datetime(2026, 9, 29, 19, 0), "33m", "x", "#000", "t"),
        belegung.Event("C", datetime(2026, 9, 29, 10, 0), datetime(2026, 9, 29, 11, 0), "Sportwelt", "x", "#000", "t"),
    ]
    frei = belegung.freie_zeiten(tag, evs, cfg, "33m", min_minuten=30)
    assert [(a.time(), b.time()) for a, b in frei] == [(time(8), time(15, 25)), (time(19), time(21))]


def test_becken_regeln(cfg):
    assert belegung.becken_fuer("Seepferdchen", cfg) == "33m"
    assert belegung.becken_fuer("Yoga im Saunagarten", cfg) == "kein Becken"
    assert belegung.kategorie_fuer("Erwachsenen-Stilschwimmen", "Schwimmschule", cfg)[0] == "Erwachsenenschwimmen"
    assert belegung.kategorie_fuer("AquaJogging", "AquaFitness", cfg)[0] == "Aquafitness"


def test_manuelle_zuordnung(tmp_path):
    belegung.save_manuell({"Babyschwimmen (3 bis 7 Monate)": "Kleinkinderbereich"})
    cfg = belegung.load_config()
    assert belegung.becken_fuer("Babyschwimmen (3 bis 7 Monate)", cfg) == "Kleinkinderbereich"
    assert belegung.becken_fuer("Babyschwimmen (7 Mon. bis 1 Jahr)", cfg) == "33m"   # exakter Name
    assert belegung.becken_nach_regel("Babyschwimmen (3 bis 7 Monate)", cfg) == "33m"
    assert "Kleinkinderbereich" in belegung.becken_auswahl(cfg)


# --- Gesamtlauf scrape.run() mit simuliertem Portal -----------------------------

def test_scrape_run(tmp_db, monkeypatch):
    import scrape

    class FakePortal:
        def __init__(self, delay):
            pass

        def fetch_tab(self, tab_id, tab_name, since, location_ids):
            if tab_name != "Schwimmschule":
                return []
            blocks, _ = portal.parse_block_list(read("block_list.html"), tab_name)
            return [b for b in blocks if b["location"] == "Nettebad"]

        def fetch_block_details(self, block_id):
            return portal.parse_block_details(read("block_details.html"))

        def fetch_oeffnungszeiten(self):
            return portal.parse_oeffnungszeiten(read("oeffnungszeiten.html"))

    monkeypatch.setattr(scrape, "Portal", FakePortal)
    logs = []
    r1 = scrape.run(log=logs.append)
    assert r1["gesehen"] == 3 and len(r1["neu"]) == 3 and r1["warnungen"] == []
    r2 = scrape.run(log=logs.append)
    assert len(r2["neu"]) == 0                 # beim zweiten Lauf nichts Neues
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 3
        assert db.get_meta(con, "website_ninjacross").startswith("Mo geschlossen")


def test_404_schaetzt_termine(tmp_db, monkeypatch):
    """Fehlt die Detailseite eines laufenden Kurses, werden Termine aus der Liste geschätzt."""
    import scrape

    class FakePortal:
        def __init__(self, delay):
            pass

        def fetch_tab(self, tab_id, tab_name, since, location_ids):
            if tab_name != "Schwimmschule":
                return []
            blocks, _ = portal.parse_block_list(read("block_list.html"), tab_name)
            return [b for b in blocks if b["block_id"] == 107489]   # Seepferdchen Di+Do 15:25

        def fetch_block_details(self, block_id):
            return None                                             # 404

        def fetch_oeffnungszeiten(self):
            return {}

    monkeypatch.setattr(scrape, "Portal", FakePortal)
    monkeypatch.setattr(db, "today", lambda: date(2026, 9, 29))
    r = scrape.run(log=lambda *_: None)
    assert any("geschätzt" in w for w in r["warnungen"])
    with db.connect() as con:
        rows = con.execute("SELECT date, start, end, status FROM sessions ORDER BY date").fetchall()
    assert len(rows) == 16                                  # Di+Do 08.09.–12.11. ohne Herbstferien
    assert tuple(rows[0]) == ("2026-09-08", "15:25", "16:10", "geschätzt")   # 45 min Standard


# --- Moskaubad --------------------------------------------------------------------

def test_moskaubad_becken(cfg):
    assert belegung.becken_fuer("Seepferdchen", cfg, "Moskaubad") == "Moskaubad"
    assert belegung.becken_fuer("Seepferdchen", cfg, "Nettebad") == "33m"
    assert belegung.becken_fuer("Yoga im Saunagarten", cfg, "Freizeitstandort Nettebad") == "kein Becken"
    assert belegung.becken_fuer("Kurs X", cfg, "Schinkelbad") == "Schinkelbad"   # nicht konfiguriert


def test_manuelle_zuordnung_pro_bad():
    # Nettebad-Zuordnung darf das Moskaubad nicht treffen – und umgekehrt
    belegung.save_manuell({"Seepferdchen": "Lehrschwimmbecken",
                           "Moskaubad: Bronze": "kein Becken"})
    cfg = belegung.load_config()
    assert belegung.becken_fuer("Seepferdchen", cfg, "Nettebad") == "Lehrschwimmbecken"
    assert belegung.becken_fuer("Seepferdchen", cfg, "Moskaubad") == "Moskaubad"
    assert belegung.becken_fuer("Bronze", cfg, "Moskaubad") == "kein Becken"
    assert belegung.becken_fuer("Bronze", cfg, "Nettebad") == "33m"
    assert belegung.manuell_schluessel("Bronze", "Moskaubad", cfg) == "Moskaubad: Bronze"
    assert belegung.manuell_schluessel("Bronze", "Nettebad", cfg) == "Bronze"


def test_oeffnungszeiten_moskaubad(cfg):
    mi, do = date(2026, 9, 30), date(2026, 10, 1)
    assert belegung.oeffnungszeiten(mi, "Moskaubad", cfg) == [(time(6), time(8)), (time(14), time(15, 30))]
    assert belegung.oeffnungszeiten(do, "Moskaubad", cfg) == []                     # Do geschlossen
    assert belegung.oeffnungszeiten(do, "33m", cfg) == [(time(8), time(21))]
    assert belegung.oeffnungszeiten(date(2026, 10, 3), "33m", cfg) == [(time(9), time(21))]  # Feiertag


def test_freie_zeiten_moskaubad(cfg):
    mi = date(2026, 9, 30)
    evs = [belegung.Event("Seepferdchen", datetime(2026, 9, 30, 14, 30), datetime(2026, 9, 30, 15, 15),
                          "Moskaubad", "x", "#000", "t")]
    frei = belegung.freie_zeiten(mi, evs, cfg, "Moskaubad", min_minuten=15)
    assert [(a.time(), b.time()) for a, b in frei] == [(time(6), time(8)), (time(14), time(14, 30)),
                                                       (time(15, 15), time(15, 30))]
    assert belegung.freie_zeiten(date(2026, 10, 1), [], cfg, "Moskaubad") == []    # Do geschlossen
