# Nettebad Osnabrück – Belegung 33-Meter-Becken

Wann ist das 33-Meter-Becken in der Erlebniswelt des [Nettebads](https://www.stadtwerke-osnabrueck.de/nettebad)
frei zum Bahnenschwimmen? Dieses kleine Python-Projekt sammelt alle Kurse (Schwimmschule, Aquafitness,
Erwachsenenschwimmen) und die Zeiten des Ninjacross-Parcours und zeigt sie in einem Wochenkalender –
inklusive einer Übersicht der freien Zeitfenster.

> **Inoffizielles Privatprojekt.** Nicht mit den Stadtwerken Osnabrück oder dem Nettebad verbunden.
> Alle Angaben ohne Gewähr – maßgeblich sind die Websites des Bads und des Buchungsportals.

| Kalender | Freie Zeiten |
|---|---|
| ![Kalenderansicht](docs/kalender.png) | ![Freie Zeitfenster](docs/freie-zeiten.png) |

## Funktionen

- **Scraper** für das [Buchungsportal der SWO-Bäder](https://www.swo-baeder-buchungsportal.de/de/bookings/blocks/):
  lädt alle Kursblöcke des Nettebads samt jedem Einzeltermin – auch Kurse, die schon laufen und nicht mehr
  buchbar sind. Abgesagte Termine werden erkannt.
- **Kurse werden nie gelöscht:** Verschwindet ein Kurs aus dem Portal, bleiben seine Termine gespeichert,
  bis der letzte vorbei ist.
- **Ninjacross-Parcours** inkl. abweichender Zeiten in den Schulferien (NDS + NRW) und an Feiertagen.
  Ändern sich die Zeiten auf der Website, weist der Scraper darauf hin.
- **Streamlit-App** mit
  - 📅 Wochenkalender (hell/dunkel, Klick auf einen Kurs öffnet ihn im Portal)
  - 🟢 freien Zeitfenstern für die nächsten 14 Tage
  - 📋 Liste aller Kursblöcke
  - 🏷️ Becken-Zuordnung per Dropdown – das Portal verrät nicht, in welchem Becken ein Kurs stattfindet

## Installation

Voraussetzung: Python 3.9 oder neuer.

```bash
git clone https://github.com/KuebelTasche/SchwimmbadSchedule.git
cd SchwimmbadSchedule
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Benutzung

```bash
python scrape.py          # Kurse aus dem Portal holen (dauert ca. 1–2 Minuten)
streamlit run app.py      # App öffnen: http://localhost:8501
```

In der App gibt es links außerdem den Button **„Neue Kurse holen“**, der dasselbe macht wie `scrape.py`.

Tests (laufen ohne Internet, mit gespeicherten HTML-Ausschnitten):

```bash
python -m pytest -q
```

## Wie es funktioniert

| Was | Quelle | Wie |
|---|---|---|
| Kurse | Buchungsportal, Liste je Kategorie | Filter auf „Nettebad“, Suchbeginn 150 Tage zurück – so kommen auch laufende Kurse mit |
| Einzeltermine | Detailseite je Kursblock | Datum, Uhrzeit von–bis, Status (z. B. „abgesagt“) |
| Ninjacross | [Öffnungszeiten Erlebniswelt](https://www.stadtwerke-osnabrueck.de/nettebad/oeffnungszeiten/erlebniswelt) | Zeiten stehen in `config.yaml`, der Scraper vergleicht den Text der Website |
| Ferien / Feiertage | `config.yaml` / Paket [`holidays`](https://pypi.org/project/holidays/) | für die abweichenden Ninjacross-Zeiten |

Der Scraper wartet zwischen zwei Seitenaufrufen (`delay_seconds` in `config.yaml`). Bitte lass das so –
einmal am Tag reicht völlig.

### Projektstruktur

| Datei | Inhalt |
|---|---|
| `scrape.py` | Einstiegspunkt zum Aktualisieren |
| `portal.py` | Abruf und Auswertung der Websites (Parser ohne Internet testbar) |
| `db.py` | SQLite-Datenbank `data/nettebad.sqlite` (`blocks` = Kursblöcke, `sessions` = Einzeltermine) |
| `belegung.py` | Becken-/Kategorie-Zuordnung, Ninjacross-Termine, freie Zeitfenster |
| `app.py` | Streamlit-App |
| `config.yaml` | Öffnungszeiten, Ninjacross, Schulferien, Farben, Becken-Regeln |
| `becken_manuell.yaml` | Becken-Zuordnungen aus der App (wird automatisch angelegt) |
| `.github/workflows/scrape.yml` | tägliches Update per GitHub Actions |
| `vendor/` | [FullCalendar](https://fullcalendar.io) 6.1.15 (MIT-Lizenz), damit die App ohne CDN läuft |

## Becken zuordnen

Das Buchungsportal gibt nur „Nettebad“ an, nicht das Becken. Standard ist deshalb: **jeder Kurs = 33m**.

- **In der App:** Reiter „🏷️ Becken zuordnen“ → Becken per Dropdown ändern → Speichern.
  Das landet in `becken_manuell.yaml` (exakter Kursname → Becken) und hat Vorrang.
- **Per Regel** für mehrere Kurse auf einmal (Teil des Kursnamens), in `config.yaml`:

  ```yaml
  becken:
    standard: "33m"
    regeln:
      "Babyschwimmen": "Kleinkinderbereich"
  ```

## Pflege

Einmal im Jahr bzw. bei Änderungen am Bad anpassen (alles in `config.yaml`):

- **Schulferien** (`schulferien`) – Niedersachsen und NRW
- **Öffnungszeiten** des 33-m-Beckens und des **Ninjacross-Parcours** – der Scraper meldet sich, wenn sich
  der Text auf der Website ändert

## Online nutzen: Streamlit Community Cloud + GitHub Actions

So ist die App vom Handy aus überall erreichbar, ohne dass ein eigener Rechner laufen muss:

1. **GitHub Actions** (`.github/workflows/scrape.yml`) führt jeden Morgen `scrape.py` aus und committet
   `data/nettebad.sqlite` zurück ins Repo. Manuell starten: *Actions → „Kurse aktualisieren“ → Run workflow*.
2. **[Streamlit Community Cloud](https://share.streamlit.io):** App aus diesem Repo anlegen, Main file `app.py`.
   Neue Commits (auch die der Action) übernimmt die App automatisch.

Hinweise:

- Die Becken-Zuordnung aus der App wird in der Cloud nur bis zum nächsten Neustart gespeichert.
  Dauerhaft: lokal zuordnen und `becken_manuell.yaml` committen.
- Vor lokaler Arbeit immer erst `git pull`, damit die Datenbank aus der Action nicht mit einer lokalen
  kollidiert. Im Zweifel die lokale Datenbank verwerfen: `git checkout -- data/nettebad.sqlite`.
- GitHub schaltet geplante Workflows nach 60 Tagen ohne Aktivität im Repo ab – die täglichen Commits der
  Action zählen als Aktivität, solange sich Kurse ändern.
- Alle Zeiten werden in deutscher Zeit (Europe/Berlin) gerechnet, auch wenn die Server in UTC laufen.
- Auf dem Handy zeigt der Kalender automatisch die Tagesansicht (umschaltbar auf 3 Tage oder Liste).

## Lokal täglich automatisch aktualisieren (optional)

Nur nötig, wenn du GitHub Actions nicht nutzt.

**macOS (launchd):** Datei `~/Library/LaunchAgents/de.nettebad.scrape.plist` anlegen, `/PFAD/ZUM/PROJEKT`
ersetzen und mit `launchctl load ~/Library/LaunchAgents/de.nettebad.scrape.plist` aktivieren:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>de.nettebad.scrape</string>
  <key>ProgramArguments</key><array>
    <string>/PFAD/ZUM/PROJEKT/.venv/bin/python</string>
    <string>/PFAD/ZUM/PROJEKT/scrape.py</string>
  </array>
  <key>WorkingDirectory</key><string>/PFAD/ZUM/PROJEKT</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>13</integer></dict>
  <key>StandardOutPath</key><string>/tmp/nettebad-scrape.log</string>
  <key>StandardErrorPath</key><string>/tmp/nettebad-scrape.log</string>
</dict></plist>
```

Hinweis: Liegt das Projekt in iCloud Drive oder „Dokumente“, braucht `launchd` ggf. „Festplattenvollzugriff“
für Python.

**Linux (cron):** `13 7 * * * cd /PFAD/ZUM/PROJEKT && .venv/bin/python scrape.py >> /tmp/nettebad-scrape.log 2>&1`
