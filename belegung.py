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


def standort_becken(location: str | None, cfg: dict) -> str:
    """Standard-Becken einer Filiale (z. B. "Moskaubad" -> "Moskaubad", "Nettebad" -> "33m")."""
    standorte = cfg.get("standorte") or {}
    if not location:
        return cfg["becken"]["standard"]
    return standorte.get(location, location)   # unbekanntes Bad: eigenes "Becken" mit seinem Namen


def ist_hauptbad(location: str | None, cfg: dict) -> bool:
    """Gehört die Filiale zum Nettebad (bzw. zum Standard-Becken)?"""
    return standort_becken(location, cfg) == cfg["becken"]["standard"]


def manuell_schluessel(kursname: str, location: str | None, cfg: dict) -> str:
    """Schlüssel in becken_manuell.yaml: Nettebad-Kurse nur mit Namen, andere Bäder mit Präfix."""
    return kursname if ist_hauptbad(location, cfg) else f"{location}: {kursname}"


def becken_nach_regel(kursname: str, cfg: dict, location: str | None = None) -> str:
    """Becken laut config.yaml (Standort, Regeln, Standard) – ohne manuelle Zuordnung."""
    if not ist_hauptbad(location, cfg):
        return standort_becken(location, cfg)   # die Nettebad-Regeln gelten dort nicht
    regeln = cfg["becken"].get("regeln") or {}
    for text, becken in regeln.items():
        if text.lower() in kursname.lower():
            return becken
    return cfg["becken"]["standard"]


def becken_auswahl(cfg: dict) -> list[str]:
    """Alle Becken, die in der App zur Auswahl stehen."""
    werte = list(cfg["becken"].get("auswahl") or [])
    for b in [cfg["becken"]["standard"], *(cfg.get("standorte") or {}).values(),
              *(cfg["becken"].get("regeln") or {}).values(),
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


def becken_fuer(kursname: str, cfg: dict, location: str | None = None) -> str:
    """Becken eines Kurses: erst manuelle Zuordnung aus der App, dann config.yaml."""
    manuell = cfg["becken"].get("manuell") or {}
    schluessel = manuell_schluessel(kursname, location, cfg)
    if schluessel in manuell:
        return manuell[schluessel]
    return becken_nach_regel(kursname, cfg, location)


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


UNBEKANNT = "06:00-22:00"   # wenn für ein Becken keine Öffnungszeiten hinterlegt sind


def _parse_ranges(text: str | None) -> list[tuple[time, time]]:
    """ "06:00-08:00, 14:00-15:30" -> [(06:00, 08:00), (14:00, 15:30)] """
    return [r for r in (_parse_range(t) for t in (text or "").split(",")) if r]


def oeffnungszeiten_plan(becken: str, cfg: dict) -> dict | None:
    plaene = cfg.get("oeffnungszeiten") or {}
    if becken in plaene:
        return plaene[becken]
    if becken == "33m" and "oeffnungszeiten_33m" in cfg:          # ältere config.yaml
        alt = cfg["oeffnungszeiten_33m"]
        return {"werktags": alt["werktags"], "wochenende": alt["wochenende_feiertag"],
                "feiertag": alt["wochenende_feiertag"]}
    return None


def zeiten_fuer_tag(plan: dict | None, wochentag: int, feiertag: bool = False) -> list[tuple[time, time]]:
    """Öffnungszeiten eines Beckens an einem Wochentag (0 = Montag)."""
    if plan is None:
        return _parse_ranges(UNBEKANNT)
    if feiertag and "feiertag" in plan:
        return _parse_ranges(plan["feiertag"])
    tag = WOCHENTAGE[wochentag]
    if tag in plan:
        return _parse_ranges(plan[tag])
    return _parse_ranges(plan.get("wochenende" if wochentag >= 5 else "werktags"))


def oeffnungszeiten(d: date, becken: str, cfg: dict) -> list[tuple[time, time]]:
    """Öffnungszeiten eines Beckens an einem Datum – leer, wenn geschlossen."""
    return zeiten_fuer_tag(oeffnungszeiten_plan(becken, cfg), d.weekday(), ist_feiertag(d))


# ---------------------------------------------------------------------------
# Beobachtete Kurse / Buchungsstatus
# ---------------------------------------------------------------------------

def wird_beobachtet(b: dict, cfg: dict) -> bool:
    for w in cfg.get("beobachten") or []:
        if (w.get("kurs", "").lower() in b["course_name"].lower()
                and (not w.get("bad") or w["bad"] == b["location"])):
            return True
    return False


def status_text(b) -> str:
    """Lesbarer Buchungsstatus, z. B. 'buchbar, 3 Plätze frei' oder 'buchbar ab 08.10.2026 18:00'."""
    status, ab, frei = b["booking_status"], b["booking_from"], b["free_places"]
    frei = int(frei) if frei is not None and frei == frei else None   # NaN aus pandas -> None
    if status == "noch nicht buchbar" and ab:
        return f"buchbar ab {datetime.fromisoformat(ab):%d.%m.%Y %H:%M}".replace(" 00:00", "")
    if status == "buchbar" and frei is not None:
        return f"buchbar, {frei} {'Platz' if frei == 1 else 'Plätze'} frei"
    return status or "unbekannt"


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
            """SELECT s.date, s.start, s.end, s.status, b.block_id, b.course_name, b.tab, b.url,
                      b.location
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
            becken_fuer(r["course_name"], cfg, r["location"]), kat, farbe, "Portal",
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
    """Zeitfenster an einem Tag, in denen im Becken nichts eingetragen ist.

    Berücksichtigt alle Öffnungsfenster des Tages (z. B. Moskaubad Mi 6–8 und 14–15:30).
    """
    frei = []
    for auf, zu in oeffnungszeiten(tag, becken, cfg):
        beginn, ende = datetime.combine(tag, auf), datetime.combine(tag, zu)
        belegt = sorted(
            (max(e.start, beginn), min(e.ende, ende))
            for e in evs
            if e.becken == becken and e.start.date() == tag and e.ende > beginn and e.start < ende
        )
        cursor = beginn
        for s_, e_ in belegt:
            if s_ > cursor:
                frei.append((cursor, s_))
            cursor = max(cursor, e_)
        if cursor < ende:
            frei.append((cursor, ende))
    return [(a, b) for a, b in frei if (b - a) >= timedelta(minutes=min_minuten)]
