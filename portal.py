"""Abruf und Auswertung der Websites.

1. Buchungsportal (swo-baeder-buchungsportal.de)
   - Kursliste pro Kategorie ("Tab"), gefiltert auf das Nettebad
   - Detailseite je Kursblock mit allen Einzelterminen
2. Öffnungszeiten-Seite des Nettebads (Ninjacross + 33-m-Becken)

Die Parse-Funktionen (parse_*) arbeiten nur auf HTML-Text und sind damit
ohne Internet testbar (siehe tests/).
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

BASE = "https://www.swo-baeder-buchungsportal.de"
OEFFNUNGSZEITEN_URL = "https://www.stadtwerke-osnabrueck.de/nettebad/oeffnungszeiten/erlebniswelt"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh) Nettebad-Belegungsplan (privates Skript, 1x taeglich)",
    "Accept-Language": "de-DE,de;q=0.9",
}

DETAILS_RE = re.compile(r"/course_blocks/details/(\d+)/")
DATE_RANGE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")
TIME_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")


def de_to_iso(d: str) -> str:
    return datetime.strptime(d.strip(), "%d.%m.%Y").date().isoformat()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_block_list(html: str, tab_name: str) -> tuple[list[dict], int | None]:
    """Liest die Kursliste. Gibt (Blöcke, Gesamtanzahl laut Seite) zurück."""
    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    for tr in soup.select("table tbody tr"):
        link = tr.select_one('td[data-title="Kurs"] a')
        if link is None:
            continue  # Suchzeile oder "keine Einträge"
        m = DETAILS_RE.search(link.get("href", ""))
        if not m:
            continue

        def cell(title: str):
            return tr.select_one(f'td[data-title="{title}"]')

        termin = cell("Termin") or cell("Termine ab")
        dr = DATE_RANGE_RE.search(termin.get_text(" ") if termin else "")
        anz_cell = cell("Anz. Termine")
        anz = re.search(r"\d+", anz_cell.get_text(" ")) if anz_cell else None
        times = list(cell("Uhrzeit").stripped_strings) if cell("Uhrzeit") else []
        weekdays = list(cell("Wochentag").stripped_strings) if cell("Wochentag") else []
        filiale = _clean(cell("Filiale").get_text(" ")) if cell("Filiale") else ""

        blocks.append({
            "block_id": int(m.group(1)),
            "course_name": _clean(link.get_text(" ")),
            "tab": tab_name,
            "location": filiale,
            "date_from": de_to_iso(dr.group(1)) if dr else None,
            "date_to": de_to_iso(dr.group(2)) if dr else None,
            "n_sessions": int(anz.group(0)) if anz else None,
            "weekdays": ", ".join(weekdays),
            "times": ", ".join(times),
            "url": f"{BASE}/de/course_blocks/details/{m.group(1)}/",
        })

    total = None
    tm = re.search(r"Einträge gesamt:\s*(\d+)", soup.get_text(" "))
    if tm:
        total = int(tm.group(1))
    return blocks, total


ABGESAGT_RE = re.compile(r"abgesagt|ausgefallen|f[äa]llt aus|entf[äa]llt|storniert", re.I)


def _status(text: str, tr) -> str:
    """Status eines Termins: 'abgesagt' (Becken frei!), 'vorbei' oder ''."""
    if ABGESAGT_RE.search(text) or tr.select_one("td.text-danger"):
        return "abgesagt"
    if "vorbei" in text.lower():
        return "vorbei"
    return ""


def parse_block_details(html: str) -> list[dict]:
    """Liest alle Einzeltermine (Datum + Uhrzeit von–bis) eines Kursblocks."""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        head = _clean(table.find("thead").get_text(" ")) if table.find("thead") else ""
        if "Datum" not in head or "Uhrzeit" not in head:
            continue
        sessions = []
        for tr in table.select("tbody tr"):
            cells = [_clean(td.get_text(" ")) for td in tr.find_all("td")]
            if len(cells) < 3:
                continue
            try:
                d = de_to_iso(cells[1])
            except ValueError:
                continue
            tr_m = TIME_RANGE_RE.search(cells[2])
            if not tr_m:
                continue
            nr = int(cells[0]) if cells[0].isdigit() else None
            sessions.append({"nr": nr, "date": d,
                             "start": tr_m.group(1).zfill(5), "end": tr_m.group(2).zfill(5),
                             "status": _status(" ".join(cells[3:]), tr)})
        return sessions
    return []


def parse_oeffnungszeiten(html: str) -> dict[str, str]:
    """Holt die Zeilen 'Ninjacross' und '33-Meter-Becken' als Text.

    Wir werten die Zeiten nicht automatisch aus (die Website schreibt sie sehr
    uneinheitlich), sondern merken uns den Text. Ändert er sich, schlägt der
    Scraper Alarm und du passt config.yaml an.
    """
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    for tr in soup.find_all("tr"):
        first = tr.find(["td", "th"])
        if not first:
            continue
        label = _clean(first.get_text(" "))
        text = " | ".join(_clean(td.get_text(" ")) for td in tr.find_all(["td", "th"])[1:])
        if label.startswith("Ninjacross"):
            result["ninjacross"] = text
        elif label.startswith("33-Meter-Becken"):
            result["33m"] = text
    return result


# ---------------------------------------------------------------------------
# Abruf
# ---------------------------------------------------------------------------

class Portal:
    def __init__(self, delay: float = 0.7):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.delay = delay

    def _get(self, url: str) -> str:
        r = self.s.get(url, timeout=30)
        r.raise_for_status()
        time.sleep(self.delay)
        return r.text

    def fetch_tab(self, tab_id: int, tab_name: str, date_from: date,
                  location_ids: list[int]) -> list[dict]:
        """Alle Kursblöcke einer Kategorie ab date_from.

        Das Portal merkt sich den Filter in der Sitzung (Cookie): Erst den Filter
        per POST setzen, dann die Liste seitenweise abrufen.
        """
        self._get(f"{BASE}/de/bookings/block_list/bookable/0/tab/{tab_id}/")
        form = [
            ("search_course_block_id", ""),
            ("search_date_from", date_from.strftime("%d.%m.%Y")),
            ("search_number_of_periods", ""),
            ("search_time_of_day", ""),
            ("search_weekday", "null"),
            ("search_free_places", ""),
            ("search_price_course", ""),
            ("active_tab_id", str(tab_id)),
        ] + [("search_location_id_list[]", str(i)) for i in location_ids]
        r = self.s.post(f"{BASE}/de/bookings/block_list_search_submit/bookable/0/tab/{tab_id}/",
                        data=form, timeout=30)
        r.raise_for_status()
        time.sleep(self.delay)

        all_blocks: list[dict] = []
        page = 1
        while page <= 30:  # Notbremse
            html = self._get(
                f"{BASE}/de/bookings/block_list/referer/user/?page={page}&items_per_page=100"
                f"&sort_field=first_start_datetime&sort_order=asc&tab={tab_id}"
            )
            blocks, total = parse_block_list(html, tab_name)
            all_blocks.extend(blocks)
            if not blocks or total is None or len(all_blocks) >= total:
                break
            page += 1
        return all_blocks

    def fetch_block_details(self, block_id: int) -> list[dict] | None:
        """Einzeltermine eines Blocks. None, wenn das Portal die Seite nicht (mehr) hat (404)."""
        try:
            return parse_block_details(self._get(f"{BASE}/de/course_blocks/details/{block_id}/"))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    def fetch_oeffnungszeiten(self) -> dict[str, str]:
        return parse_oeffnungszeiten(self._get(OEFFNUNGSZEITEN_URL))
