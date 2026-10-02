"""Holt neue Kurse aus dem Buchungsportal und speichert sie.

Aufruf:   python scrape.py
Dauer:    ca. 1–2 Minuten (wir warten bewusst zwischen den Seitenaufrufen)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta

import requests

import db
from belegung import load_config, status_text, wird_beobachtet
from portal import Portal


WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def schaetze_termine(con, block) -> list[dict]:
    """Wöchentliche Termine aus 'von–bis', Wochentagen und Uhrzeiten der Kursliste.

    Die Dauer nehmen wir von anderen Blöcken desselben Kurses, sonst aus dem Namen
    ("(30 min.)"), sonst 45 Minuten. Ferien-Ausfälle kennen wir so natürlich nicht.
    """
    if block is None or not block["date_from"] or not block["date_to"]:
        return []
    tage = [WOCHENTAGE.index(w.strip()) for w in (block["weekdays"] or "").split(",")
            if w.strip() in WOCHENTAGE]
    zeiten = [z.strip() for z in (block["times"] or "").split(",") if z.strip()]
    if not tage or not zeiten:
        return []
    if len(zeiten) < len(tage):
        zeiten += [zeiten[-1]] * (len(tage) - len(zeiten))

    vorbild = con.execute(
        """SELECT s.start, s.end FROM sessions s JOIN blocks b USING (block_id)
           WHERE b.course_name=? AND s.status!='geschätzt' LIMIT 1""",
        (block["course_name"],),
    ).fetchone()
    if vorbild:
        dauer = (datetime.strptime(vorbild["end"], "%H:%M") - datetime.strptime(vorbild["start"], "%H:%M"))
    else:
        m = re.search(r"(\d+)\s*min", block["course_name"])
        dauer = timedelta(minutes=int(m.group(1)) if m else 45)

    kandidaten, d = [], date.fromisoformat(block["date_from"])
    ende = date.fromisoformat(block["date_to"])
    while d <= ende:
        if d.weekday() in tage:
            kandidaten.append(d)
        d += timedelta(days=1)

    # Zu viele Termine? Dann fallen vermutlich die niedersächsischen Schulferien aus.
    if block["n_sessions"] and len(kandidaten) > block["n_sessions"] and "Ferien" not in block["course_name"]:
        cfg = load_config()
        nds = [(date.fromisoformat(str(f["von"])), date.fromisoformat(str(f["bis"])))
               for f in cfg.get("schulferien", []) if "NDS" in f["name"]]
        kandidaten = [k for k in kandidaten if not any(a <= k <= b for a, b in nds)]

    out = []
    for nr, d in enumerate(kandidaten, 1):
        start = datetime.strptime(zeiten[tage.index(d.weekday())], "%H:%M")
        out.append({"nr": nr, "date": d.isoformat(), "start": start.strftime("%H:%M"),
                    "end": (start + dauer).strftime("%H:%M"), "status": "geschätzt"})
    return out


# ---------------------------------------------------------------------------
# Beobachtete Kurse -> Meldungen (werden von der GitHub Action als Issue verschickt)
# ---------------------------------------------------------------------------

def _datum(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%d.%m.%Y") if iso else "?"


def meldung_text(art: str, b: dict, vorher: str | None = None) -> str:
    kopf = {"neu": "🆕 **Neuer Kursblock**",
            "buchbar": f"✅ **Jetzt buchbar** (vorher: {vorher})"}[art]
    tage = " / ".join(f"{w[:2]} {z}" for w, z in zip(b["weekdays"].split(", "), b["times"].split(", ")))
    return (f"- {kopf}: {b['course_name']} ({b['location']}) · {tage} · "
            f"{_datum(b['date_from'])}–{_datum(b['date_to'])} · {b['n_sessions'] or '?'} Termine · "
            f"{status_text(b)} · [im Portal öffnen]({b['url']})")


def run(log=print) -> dict:
    cfg = load_config()
    pcfg = cfg["portal"]
    today = db.today()
    since = today - timedelta(days=int(pcfg.get("days_back", 150)))
    names = set(pcfg["location_names"])
    portal = Portal(delay=float(pcfg.get("delay_seconds", 0.7)))
    run_start = db.now_iso()

    neu, warnungen, gesehen, meldungen = [], [], 0, []
    with db.connect() as con:
        # 1) Kursblöcke je Kategorie
        for tab_id, tab_name in pcfg["tabs"].items():
            log(f"· Lade Kategorie '{tab_name}' …")
            try:
                blocks = portal.fetch_tab(int(tab_id), tab_name, since, pcfg["location_ids"])
            except requests.RequestException as e:
                warnungen.append(f"Kategorie {tab_name} konnte nicht geladen werden: {e}")
                continue
            fremde = [b for b in blocks if b["location"] not in names]
            if fremde:
                warnungen.append(
                    f"Filter im Portal hat bei '{tab_name}' nicht gegriffen (auch andere Bäder "
                    f"in der Liste). Bereits laufende Kurse könnten fehlen."
                )
            for b in blocks:
                if b["location"] not in names:
                    continue
                gesehen += 1
                vorher = db.get_block(con, b["block_id"])
                ist_neu = db.upsert_block(con, b)
                if ist_neu:
                    neu.append(b)
                # Beobachtete Kurse: neuer Block oder gerade buchbar geworden?
                if wird_beobachtet(b, cfg) and (b["date_to"] or "") >= today.isoformat():
                    if ist_neu:
                        meldungen.append(meldung_text("neu", b))
                    elif (vorher is not None and vorher["booking_status"] is not None
                          and vorher["booking_status"] != "buchbar" and b["booking_status"] == "buchbar"):
                        meldungen.append(meldung_text("buchbar", b, vorher["booking_status"]))
            con.commit()

        # 2) Einzeltermine für alle Blöcke, die noch nicht vorbei sind
        ids = db.blocks_needing_details(con, today.isoformat())
        log(f"· Lade Einzeltermine für {len(ids)} Kursblöcke …")
        geschaetzt, fehler = [], []
        for i, block_id in enumerate(ids, 1):
            try:
                sessions = portal.fetch_block_details(block_id)
            except requests.RequestException as e:
                fehler.append(f"{block_id} ({e.__class__.__name__})")
                continue
            if not sessions:
                # Detailseite fehlt/leer: Termine aus Wochentag + Uhrzeit der Liste schätzen,
                # aber nur, wenn wir noch keine echten Termine gespeichert haben.
                hat_termine = con.execute("SELECT 1 FROM sessions WHERE block_id=? LIMIT 1",
                                          (block_id,)).fetchone()
                if not hat_termine:
                    sessions = schaetze_termine(con, db.get_block(con, block_id))
                    if sessions:
                        geschaetzt.append(block_id)
            db.replace_sessions(con, block_id, sessions or [])
            if i % 10 == 0:
                con.commit()
                log(f"  {i}/{len(ids)}")
        if geschaetzt:
            warnungen.append(f"{len(geschaetzt)} Kurs(e) ohne Detailseite – Termine aus der Kursliste "
                             f"geschätzt (Blöcke {', '.join(map(str, geschaetzt))}).")
        if fehler:
            warnungen.append(f"Termine für {len(fehler)} Kurs(e) nicht ladbar: {', '.join(fehler)}")

        # 3) Öffnungszeiten-Seite: hat sich bei Ninjacross / 33-m-Becken etwas geändert?
        log("· Prüfe Öffnungszeiten (Ninjacross, 33-m-Becken) …")
        try:
            oz = portal.fetch_oeffnungszeiten()
            if not oz:
                warnungen.append("Öffnungszeiten-Seite: Tabelle nicht gefunden – bitte manuell prüfen.")
            for key, text in oz.items():
                alt = db.get_meta(con, f"website_{key}")
                if alt is not None and alt != text:
                    warnungen.append(
                        f"Die Website zeigt neue Zeiten für '{key}': „{text}“ "
                        f"(vorher: „{alt}“). Bitte config.yaml anpassen."
                    )
                db.set_meta(con, f"website_{key}", text)
        except requests.RequestException as e:
            warnungen.append(f"Öffnungszeiten-Seite nicht erreichbar: {e}")

        # 4) Kurse, die nicht mehr im Portal stehen, aber noch laufen
        verschwunden = con.execute(
            """SELECT block_id, course_name, date_to FROM blocks
               WHERE last_seen < ? AND date_to >= ? ORDER BY date_to""",
            (run_start, today.isoformat()),
        ).fetchall()

        db.set_meta(con, "last_run", db.now_iso())
        db.set_meta(con, "last_warnings", json.dumps(warnungen, ensure_ascii=False))

    # Zusammenfassung
    log("")
    log(f"Fertig. {gesehen} Nettebad-Kursblöcke im Portal, davon {len(neu)} neu.")
    for b in neu:
        log(f"  + {b['course_name']} ({b['weekdays']} {b['times']}, "
            f"{b['date_from']} bis {b['date_to']}, Block {b['block_id']})")
    if verschwunden:
        log(f"{len(verschwunden)} Kurse stehen nicht mehr im Portal, bleiben aber bis zum Ende gespeichert:")
        for r in verschwunden:
            log(f"  · {r['course_name']} (Block {r['block_id']}, bis {r['date_to']})")
    for m in meldungen:
        log(f"🔔 {m}")
    for w in warnungen:
        log(f"⚠ {w}")
    return {"neu": neu, "warnungen": warnungen, "gesehen": gesehen, "meldungen": meldungen}


def schreibe_meldungen(pfad: str, meldungen: list[str], cfg: dict) -> None:
    """Meldungen als Markdown-Datei (Text für das GitHub-Issue). Ohne Meldungen: keine Datei."""
    if not meldungen:
        return
    with open(pfad, "w", encoding="utf-8") as f:
        f.write("Beim täglichen Abgleich mit dem Buchungsportal ist mir aufgefallen:\n\n")
        f.write("\n".join(meldungen) + "\n\n")
        if cfg.get("benachrichtigen"):
            f.write(f"{cfg['benachrichtigen']}\n\n")
        f.write("_Beobachtete Kurse stehen in `config.yaml` unter `beobachten`. "
                "Dieses Issue einfach schließen, wenn erledigt._\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Neue Kurse aus dem Buchungsportal holen.")
    parser.add_argument("--meldungen", metavar="DATEI",
                        help="Meldungen zu beobachteten Kursen als Markdown in diese Datei schreiben")
    args = parser.parse_args()
    result = run()
    if args.meldungen:
        schreibe_meldungen(args.meldungen, result["meldungen"], load_config())
    sys.exit(0 if result["gesehen"] else 1)
