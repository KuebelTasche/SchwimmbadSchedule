"""Aus Datenbank + config.yaml die Belegung des Beckens berechnen.

- events(): alle belegten Zeiten (Kurse + Ninjacross) in einem Datumsbereich
- freie_zeiten(): Lücken innerhalb der Öffnungszeiten, in denen nichts läuft
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

import holidays
import yaml

import db

CONFIG_PATH = Path(__file__).parent / "config.yaml"
# Becken, die du in der App von Hand zugeordnet hast (Kursname -> Becken).
# Wird von der App geschrieben, darf aber auch von Hand bearbeitet werden.
MANUELL_PATH = Path(__file__).parent / "becken_manuell.yaml"
WOCHENTAGE = ["mo", "di", "mi", "do", "fr", "sa", "so"]


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["becken"]["manuell"] = load_manuell()
    return cfg


def load_manuell(path: Path | None = None) -> dict[str, str]:
    path = Path(path or MANUELL_PATH)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_manuell(zuordnung: dict[str, str], path: Path | None = None) -> None:
    path = Path(path or MANUELL_PATH)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Von Hand zugeordnete Becken (exakter Kursname -> Becken).\n"
                "# Wird von der App unter „Becken zuordnen“ gepflegt. Hat Vorrang vor config.yaml.\n")
        yaml.safe_dump(dict(sorted(zuordnung.items())), f, allow_unicode=True, sort_keys=False)


def becken_nach_regel(kursname: str, cfg: dict) -> str:
    """Becken laut config.yaml (Regeln bzw. Standard) – ohne manuelle Zuordnung."""
    regeln = cfg["becken"].get("regeln") or {}
    for text, becken in regeln.items():
        if text.lower() in kursname.lower():
            return becken
    return cfg["becken"]["standard"]


def becken_auswahl(cfg: dict) -> list[str]:
    """Alle Becken, die in der App zur Auswahl stehen."""
    werte = list(cfg["becken"].get("auswahl") or [])
    for b in [cfg["becken"]["standard"], *(cfg["becken"].get("regeln") or {}).values(),
              *(cfg["becken"].get("manuell") or {}).values()]:
        if b not in werte:
            werte.append(b)
    return werte


@dataclass
class Event:
    titel: str
    start: datetime
    ende: datetime
    becken: str
    kategorie: str
    farbe: str
    quelle: str               # "Portal" oder "Website"
    block_id: int | None = None
    url: str | None = None


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _parse_range(text: str | None) -> tuple[time, time] | None:
    if not text:
        return None
    a, b = text.split("-")
    return time.fromisoformat(a.strip().zfill(5)), time.fromisoformat(b.strip().zfill(5))


@lru_cache(maxsize=None)
def _feiertage(jahr: int):
    return holidays.Germany(subdiv="NI", years=jahr)


def ist_feiertag(d: date) -> bool:
    return d in _feiertage(d.year)


def ist_ferien(d: date, cfg: dict) -> bool:
    for f in cfg.get("schulferien", []):
        if _as_date(f["von"]) <= d <= _as_date(f["bis"]):
            return True
    return False


def _as_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def becken_fuer(kursname: str, cfg: dict) -> str:
    """Becken eines Kurses: erst manuelle Zuordnung aus der App, dann config.yaml."""
    manuell = cfg["becken"].get("manuell") or {}
    if kursname in manuell:
        return manuell[kursname]
    return becken_nach_regel(kursname, cfg)


def kategorie_fuer(kursname: str, tab: str | None, cfg: dict) -> tuple[str, str]:
    for k in cfg["kategorien"]:
        for bed in k["wenn"]:
            if bed.startswith("tab:"):
                if tab and tab == bed[4:]:
                    return k["name"], k["farbe"]
            elif bed.lower() in kursname.lower():
                return k["name"], k["farbe"]
    letzte = cfg["kategorien"][-1]
    return letzte["name"], letzte["farbe"]


def oeffnungszeit_33m(d: date, cfg: dict) -> tuple[time, time]:
    oz = cfg["oeffnungszeiten_33m"]
    key = "wochenende_feiertag" if d.weekday() >= 5 or ist_feiertag(d) else "werktags"
    return _parse_range(oz[key])


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def ninja_events(von: date, bis: date, cfg: dict) -> list[Event]:
    n = cfg["ninjacross"]
    name, farbe = kategorie_fuer("Ninjacross", None, cfg)
    out = []
    d = von
    while d <= bis:
        plan = n["ferien"] if ist_ferien(d, cfg) else n["normal"]
        tag = WOCHENTAGE[d.weekday()]
        # Montags bleibt der Parcours auch an Feiertagen zu ("montags geschlossen")
        if ist_feiertag(d) and tag != "mo":
            tag = "feiertag"
        r = _parse_range(plan.get(tag))
        if r:
            out.append(Event("Ninjacross-Parcours", datetime.combine(d, r[0]),
                             datetime.combine(d, r[1]), n.get("becken", "33m"),
                             name, farbe, "Website"))
        d += timedelta(days=1)
    return out


def kurs_events(von: date, bis: date, cfg: dict) -> list[Event]:
    with db.connect() as con:
        rows = con.execute(
            """SELECT s.date, s.start, s.end, s.status, b.block_id, b.course_name, b.tab, b.url
               FROM sessions s JOIN blocks b USING (block_id)
               WHERE s.date BETWEEN ? AND ?
                 AND COALESCE(s.status, '') != 'abgesagt'   -- abgesagt = Becken frei
               ORDER BY s.date, s.start""",
            (von.isoformat(), bis.isoformat()),
        ).fetchall()
    out = []
    for r in rows:
        d = date.fromisoformat(r["date"])
        kat, farbe = kategorie_fuer(r["course_name"], r["tab"], cfg)
        out.append(Event(
            r["course_name"] + (" (geschätzt)" if r["status"] == "geschätzt" else ""),
            datetime.combine(d, time.fromisoformat(r["start"])),
            datetime.combine(d, time.fromisoformat(r["end"])),
            becken_fuer(r["course_name"], cfg), kat, farbe, "Portal",
            r["block_id"], r["url"],
        ))
    return out


def events(von: date, bis: date, cfg: dict | None = None) -> list[Event]:
    cfg = cfg or load_config()
    return sorted(kurs_events(von, bis, cfg) + ninja_events(von, bis, cfg), key=lambda e: e.start)


# ---------------------------------------------------------------------------
# Freie Zeitfenster
# ---------------------------------------------------------------------------

def freie_zeiten(tag: date, evs: list[Event], cfg: dict, becken: str = "33m",
                 min_minuten: int = 30) -> list[tuple[datetime, datetime]]:
    """Zeitfenster an einem Tag, in denen im Becken nichts eingetragen ist."""
    auf, zu = oeffnungszeit_33m(tag, cfg)
    beginn, ende = datetime.combine(tag, auf), datetime.combine(tag, zu)
    belegt = sorted(
        (max(e.start, beginn), min(e.ende, ende))
        for e in evs
        if e.becken == becken and e.start.date() == tag and e.ende > beginn and e.start < ende
    )
    frei, cursor = [], beginn
    for s, e in belegt:
        if s > cursor:
            frei.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < ende:
        frei.append((cursor, ende))
    return [(a, b) for a, b in frei if (b - a) >= timedelta(minutes=min_minuten)]
