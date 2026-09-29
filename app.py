"""Streamlit-Frontend: Wann ist das 33-Meter-Becken im Nettebad belegt?

Start:  streamlit run app.py
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import belegung
import db

st.set_page_config(page_title="Nettebad – 33-m-Becken", page_icon="🏊", layout="wide")

WOCHENTAG_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
VENDOR = Path(__file__).parent / "vendor"
# Streamlit Community Cloud legt den Code unter /mount/src/ ab – dort ist das Dateisystem nicht dauerhaft
IN_DER_CLOUD = str(Path(__file__).resolve()).startswith("/mount/src/")

cfg = belegung.load_config()
heute = db.today()   # deutsche Zeit, auch wenn der Server in UTC läuft

# ---------------------------------------------------------------------------
# Seitenleiste
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("🏊 Nettebad")

    with db.connect() as con:
        last_run = db.get_meta(con, "last_run")
        warnungen = json.loads(db.get_meta(con, "last_warnings") or "[]")
        alle_becken = sorted({belegung.becken_fuer(r["course_name"], cfg)
                              for r in con.execute("SELECT course_name FROM blocks")}
                             | {cfg["becken"]["standard"], cfg["ninjacross"]["becken"]})

    becken = st.selectbox("Becken", alle_becken,
                          index=alle_becken.index(cfg["becken"]["standard"]))
    kat_namen = [k["name"] for k in cfg["kategorien"]]
    kategorien = st.multiselect("Kategorien anzeigen", kat_namen, default=kat_namen)

    st.divider()
    if last_run:
        st.caption(f"Zuletzt aktualisiert: {datetime.fromisoformat(last_run):%d.%m.%Y %H:%M}")
    else:
        st.info("Noch keine Daten – einmal aktualisieren.")

    if st.button("🔄 Neue Kurse holen", use_container_width=True):
        import scrape
        with st.status("Lade Kurse aus dem Buchungsportal …", expanded=True) as status:
            ergebnis = scrape.run(log=st.write)
            status.update(label=f"Fertig – {len(ergebnis['neu'])} neue Kursblöcke", state="complete")
        st.rerun()

    if warnungen:
        with st.expander(f"⚠️ {len(warnungen)} Hinweis(e) vom letzten Lauf"):
            for w in warnungen:
                st.caption(w)

# ---------------------------------------------------------------------------
# Daten laden
# ---------------------------------------------------------------------------
von, bis = heute - timedelta(days=14), heute + timedelta(days=200)
alle_events = belegung.events(von, bis, cfg)
events = [e for e in alle_events if e.becken == becken and e.kategorie in kategorien]

st.markdown(f"### 🏊 Belegung {becken}-Becken")

tab_kal, tab_frei, tab_kurse, tab_becken = st.tabs(
    ["📅 Kalender", "🟢 Freie Zeiten", "📋 Alle Kurse", "🏷️ Becken zuordnen"])

# ---------------------------------------------------------------------------
# Kalender (FullCalendar, im Browser gerendert)
# ---------------------------------------------------------------------------
with tab_kal:
    legende = " &nbsp; ".join(
        f'<span style="background:{k["farbe"]};color:white;padding:2px 8px;border-radius:4px">{k["name"]}</span>'
        for k in cfg["kategorien"] if k["name"] in kategorien
    )
    st.markdown(legende, unsafe_allow_html=True)

    fc_events = [{
        "title": e.titel,
        "start": e.start.isoformat(),
        "end": e.ende.isoformat(),
        "color": e.farbe,
        "url": e.url,
        "extendedProps": {"kategorie": e.kategorie, "quelle": e.quelle},
    } for e in events]

    # Öffnungszeiten als "businessHours": außerhalb wird grau hinterlegt
    oz = cfg["oeffnungszeiten_33m"]
    w_auf, w_zu = oz["werktags"].split("-")
    we_auf, we_zu = oz["wochenende_feiertag"].split("-")
    business = [
        {"daysOfWeek": [1, 2, 3, 4, 5], "startTime": w_auf, "endTime": w_zu},
        {"daysOfWeek": [0, 6], "startTime": we_auf, "endTime": we_zu},
    ]
    # Hell oder dunkel? Der Kalender läuft in einem eigenen iframe und bekommt das
    # Streamlit-Theme nicht automatisch mit – deshalb geben wir es ihm mit.
    try:
        theme = st.context.theme.type or "auto"   # "light" | "dark" (ab Streamlit 1.46)
    except Exception:
        theme = "auto"

    # Schulferien als Hinweis oben im Tag
    ferien = [{
        "title": f["name"], "start": str(f["von"]),
        "end": str(belegung._as_date(f["bis"]) + timedelta(days=1)),
        "display": "background", "color": "#ffd43b", "allDay": True,
    } for f in cfg.get("schulferien", [])]

    # FullCalendar liegt im Ordner vendor/ – so klappt's auch ohne Internet
    fc_js = (VENDOR / "fullcalendar-6.1.15.global.min.js").read_text(encoding="utf-8")
    fc_de = (VENDOR / "fullcalendar-locale-de.global.min.js").read_text(encoding="utf-8")

    dunkel_css = """
        --fc-page-bg-color: #0e1117;
        --fc-neutral-bg-color: #262730;
        --fc-neutral-text-color: #c9ccd3;
        --fc-border-color: #3b3f4a;
        --fc-non-business-color: rgba(255, 255, 255, 0.07);
        --fc-today-bg-color: rgba(77, 171, 247, 0.14);
        --fc-list-event-hover-bg-color: #262730;
        --fc-bg-event-opacity: 0.25;
        color: #fafafa; background: #0e1117;
    """

    html = f"""
    <div id="cal" class="theme-{theme}"></div>
    <script>{fc_js}</script>
    <script>{fc_de}</script>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; }}
      #cal {{ --fc-today-bg-color: rgba(34, 139, 230, 0.07); color: #212529; background: #fff; }}
      #cal.theme-dark {{ {dunkel_css} }}
      @media (prefers-color-scheme: dark) {{ #cal.theme-auto {{ {dunkel_css} }} }}
      body:has(#cal.theme-dark) {{ background: #0e1117; }}
      .fc .fc-timegrid-slot {{ height: 1.9em; }}
      .fc .fc-timegrid-slot-label {{ vertical-align: top; }}
      .fc .fc-timegrid-slot-label-cushion {{ font-size: 0.8em; font-variant-numeric: tabular-nums; padding: 0 6px; }}
      .fc .zeit-halb {{ opacity: 0.55; }}
      .fc .zeit-voll {{ font-weight: 600; }}
      .fc .fc-toolbar-title {{ font-size: 1.3em; }}
      .fc-event {{ font-size: 0.78em; cursor: pointer; }}
      /* Handy: kompaktere Kopfzeile */
      @media (max-width: 640px) {{
        .fc .fc-toolbar-title {{ font-size: 1.05em; }}
        .fc .fc-button {{ padding: 0.3em 0.55em; font-size: 0.85em; }}
        .fc .fc-toolbar.fc-header-toolbar {{ margin-bottom: 0.6em; }}
        .fc-event {{ font-size: 0.85em; }}
      }}
    </style>
    <script>
      // Schmaler Bildschirm (Handy)? Dann Tagesansicht statt Woche.
      const schmal = () => window.innerWidth < 640;
      const toolbars = () => schmal()
        ? {{ header: {{ left: 'prev,next', center: 'title', right: 'today' }},
             footer: {{ center: 'timeGridDay,timeGrid3,listWeek' }} }}
        : {{ header: {{ left: 'prev,next today', center: 'title', right: 'timeGridWeek,timeGridDay,listWeek' }},
             footer: false }};
      const cal = new FullCalendar.Calendar(document.getElementById('cal'), {{
        locale: 'de',
        initialView: schmal() ? 'timeGridDay' : 'timeGridWeek',
        firstDay: 1,
        views: {{ timeGrid3: {{ type: 'timeGrid', duration: {{ days: 3 }}, buttonText: '3 Tage' }} }},
        headerToolbar: toolbars().header,
        footerToolbar: toolbars().footer,
        windowResize: () => {{
          const t = toolbars();
          cal.setOption('headerToolbar', t.header);
          cal.setOption('footerToolbar', t.footer);
          const v = cal.view.type;
          if (schmal() && v === 'timeGridWeek') cal.changeView('timeGridDay');
          if (!schmal() && (v === 'timeGridDay' || v === 'timeGrid3')) cal.changeView('timeGridWeek');
        }},
        slotMinTime: '06:00:00',
        slotMaxTime: '21:30:00',
        slotDuration: '00:30:00',
        slotLabelInterval: '00:30',
        slotLabelFormat: {{ hour: '2-digit', minute: '2-digit', hour12: false }},
        slotLabelClassNames: (arg) => arg.date.getMinutes() === 0 ? ['zeit-voll'] : ['zeit-halb'],
        eventTimeFormat: {{ hour: '2-digit', minute: '2-digit', hour12: false }},
        allDaySlot: true,
        allDayText: 'Ferien',
        nowIndicator: true,
        businessHours: {json.dumps(business)},
        height: 'auto',
        events: {json.dumps(fc_events + ferien)},
        eventDidMount: (info) => {{
          const p = info.event.extendedProps;
          if (p.kategorie) info.el.title = info.event.title + ' (' + p.kategorie + ')';
        }},
        eventClick: (info) => {{
          info.jsEvent.preventDefault();
          if (info.event.url) window.open(info.event.url, '_blank');
        }},
      }});
      cal.render();
    </script>
    """
    components.html(html, height=1180, scrolling=True)
    st.caption("Tipp: Klick auf einen Kurs öffnet ihn im Buchungsportal. "
               "Grau = Becken geschlossen, gelb = Schulferien (Ninjacross hat dann andere Zeiten).")

# ---------------------------------------------------------------------------
# Freie Zeiten
# ---------------------------------------------------------------------------
with tab_frei:
    c1, c2 = st.columns(2)
    start_tag = c1.date_input("Ab", heute, format="DD.MM.YYYY")
    min_min = c2.slider("Mindestens so lange frei (Minuten)", 15, 180, 45, step=15)
    jetzt = db.now()

    def stunde(t: datetime) -> float:
        return t.hour + t.minute / 60

    tage_info, balken = [], []
    for i in range(14):
        tag = start_tag + timedelta(days=i)
        label = (f"{WOCHENTAG_DE[tag.weekday()]} {tag:%d.%m.}"
                 + (" 🎒" if belegung.ist_ferien(tag, cfg) else "")
                 + (" 🎉" if belegung.ist_feiertag(tag) else ""))
        frei = belegung.freie_zeiten(tag, events, cfg, becken, min_min)
        if tag == heute:
            # Heute: nur, was noch kommt (ab jetzt)
            frei = [(max(a, jetzt), b) for a, b in frei
                    if b - max(a, jetzt) >= timedelta(minutes=min_min)]
        belegt = [e for e in events if e.start.date() == tag]
        for a, b in frei:
            balken.append({"Tag": label, "von": stunde(a), "bis": stunde(b), "Art": "frei",
                           "Info": f"frei {a:%H:%M}–{b:%H:%M}"})
        for e in belegt:
            balken.append({"Tag": label, "von": stunde(e.start), "bis": stunde(e.ende),
                           "Art": e.kategorie, "Info": f"{e.start:%H:%M}–{e.ende:%H:%M} {e.titel}"})
        tage_info.append((tag, label, frei, belegt))

    # Zeitleiste: jede Zeile ein Tag, grün = frei, bunt = belegt
    if balken:
        import altair as alt
        farben = {k["name"]: k["farbe"] for k in cfg["kategorien"]}
        farben["frei"] = "#b2f2bb"
        tage = [t[1] for t in tage_info]
        # Reihenfolge fürs Zeichnen: frei ganz unten, Ninjacross darüber, Kurse oben drauf
        rang = {"frei": 0, "Ninjacross": 1}
        balken.sort(key=lambda b: rang.get(b["Art"], 2))
        chart = (
            alt.Chart(pd.DataFrame(balken))
            .mark_bar(cornerRadius=3, opacity=0.9)
            .encode(
                x=alt.X("von:Q", title=None, scale=alt.Scale(domain=[6.5, 21.5]),
                        axis=alt.Axis(values=list(range(7, 22)), format="d", labelOverlap=True,
                                      labelExpr="datum.value + ':00'", orient="top")),
                x2="bis:Q",
                y=alt.Y("Tag:N", sort=tage, title=None),
                color=alt.Color("Art:N", scale=alt.Scale(domain=list(farben), range=list(farben.values())),
                                legend=alt.Legend(orient="bottom", title=None, columns=3)),
                tooltip=["Info:N"],
            )
            .properties(height=30 * len(tage))
        )
        st.altair_chart(chart, use_container_width=True)

    # Kompakte Liste – funktioniert auch auf dem Handy gut
    st.markdown("##### Freie Zeitfenster")
    for tag, label, frei, belegt in tage_info:
        titel = ("**Heute** · " if tag == heute else "") + f"**{label}**"
        frei_text = " · ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in frei) or "nichts frei"
        with st.expander(f"{titel} — 🟢 {frei_text}", expanded=(tag == heute)):
            if belegt:
                for e in belegt:
                    st.markdown(f"<span style='color:{e.farbe}'>■</span> "
                                f"{e.start:%H:%M}–{e.ende:%H:%M} &nbsp;{e.titel}", unsafe_allow_html=True)
            else:
                st.caption("Nichts eingetragen.")
    st.caption("🎒 = Schulferien, 🎉 = Feiertag. „Frei“ heißt: kein Kurs und kein Ninjacross "
               "eingetragen – andere Badegäste sind natürlich trotzdem da.")

# ---------------------------------------------------------------------------
# Alle Kurse
# ---------------------------------------------------------------------------
with tab_kurse:
    with db.connect() as con:
        df = pd.read_sql_query(
            """SELECT b.*, COUNT(s.date) AS termine_gespeichert,
                      SUM(COALESCE(s.status, '') = 'abgesagt') AS abgesagt
               FROM blocks b LEFT JOIN sessions s USING (block_id)
               GROUP BY b.block_id ORDER BY b.date_from""", con)
    if df.empty:
        st.info("Noch keine Kurse gespeichert.")
    else:
        df["Becken"] = df["course_name"].map(lambda n: belegung.becken_fuer(n, cfg))
        df["Kategorie"] = [belegung.kategorie_fuer(n, t, cfg)[0] for n, t in zip(df["course_name"], df["tab"])]
        df["Status"] = df.apply(
            lambda r: "vorbei" if r["date_to"] < heute.isoformat()
            else ("läuft" if r["date_from"] <= heute.isoformat() else "kommt"), axis=1)
        df["Im Portal"] = df["last_seen"].map(
            lambda s: "ja" if s[:10] == (last_run or "")[:10] else f"zuletzt {s[:10]}")
        nur_aktuell = st.checkbox("Vergangene Kurse ausblenden", value=True)
        if nur_aktuell:
            df = df[df["Status"] != "vorbei"]
        df["date_from"] = pd.to_datetime(df["date_from"])
        df["date_to"] = pd.to_datetime(df["date_to"])
        st.dataframe(
            df[["course_name", "Kategorie", "Becken", "weekdays", "times", "date_from", "date_to",
                "n_sessions", "termine_gespeichert", "abgesagt", "Status", "Im Portal", "url"]],
            hide_index=True, use_container_width=True,
            column_config={
                "course_name": "Kurs", "weekdays": "Wochentag(e)", "times": "Uhrzeit",
                "date_from": st.column_config.DateColumn("von", format="DD.MM.YYYY"),
                "date_to": st.column_config.DateColumn("bis", format="DD.MM.YYYY"),
                "n_sessions": "Termine", "termine_gespeichert": "gespeichert",
                "url": st.column_config.LinkColumn("Portal", display_text="öffnen"),
            },
        )
        st.caption(f"{len(df)} Kursblöcke. Becken falsch? Im Reiter „🏷️ Becken zuordnen“ umstellen.")

# ---------------------------------------------------------------------------
# Becken zuordnen: Kurse per Dropdown aus dem 33-m-Becken "rausschmeißen"
# ---------------------------------------------------------------------------
with tab_becken:
    st.markdown("Findet ein Kurs in Wirklichkeit woanders statt? Hier das Becken umstellen und "
                "**Speichern** klicken. Die Zuordnung gilt für alle Blöcke dieses Kurses – auch "
                "für zukünftige, die der Scraper später findet.")

    if IN_DER_CLOUD:
        st.warning("Die App läuft in der Streamlit Cloud: Änderungen hier gelten nur bis zum nächsten "
                   "Neustart der App. Dauerhaft: lokal zuordnen und `becken_manuell.yaml` pushen.", icon="☁️")

    with db.connect() as con:
        kb = pd.read_sql_query(
            "SELECT course_name, tab, weekdays, times, date_to FROM blocks", con)

    if kb.empty:
        st.info("Noch keine Kurse gespeichert.")
    else:
        kurz = {"Montag": "Mo", "Dienstag": "Di", "Mittwoch": "Mi", "Donnerstag": "Do",
                "Freitag": "Fr", "Samstag": "Sa", "Sonntag": "So"}
        aktuell = kb[kb["date_to"] >= heute.isoformat()]
        zeilen = []
        for name, gruppe in kb.groupby("course_name"):
            laufend = aktuell[aktuell["course_name"] == name]
            slots = []
            for wds, zs in zip(laufend["weekdays"], laufend["times"]):
                for wd, z in zip((wds or "").split(", "), (zs or "").split(", ")):
                    s = f"{kurz.get(wd, wd)} {z}"
                    if s.strip() and s not in slots:
                        slots.append(s)
            zeilen.append({
                "Kurs": name,
                "Kategorie": belegung.kategorie_fuer(name, gruppe["tab"].iloc[0], cfg)[0],
                "Aktuelle Termine": ", ".join(sorted(slots, key=lambda x: (list(kurz.values()).index(x[:2])
                                                                          if x[:2] in kurz.values() else 9, x))),
                "Blöcke": len(laufend),
                "Becken": belegung.becken_fuer(name, cfg),
            })
        tabelle = pd.DataFrame(zeilen).sort_values(["Kategorie", "Kurs"]).reset_index(drop=True)

        nur_laufende = st.checkbox("Nur Kurse mit aktuellen oder kommenden Terminen", value=True)
        if nur_laufende:
            tabelle = tabelle[tabelle["Blöcke"] > 0].reset_index(drop=True)

        bearbeitet = st.data_editor(
            tabelle,
            hide_index=True, use_container_width=True, key=f"becken_editor_{nur_laufende}",
            height=35 * (len(tabelle) + 1) + 3,     # alle Kurse ohne Scrollen
            disabled=["Kurs", "Kategorie", "Aktuelle Termine", "Blöcke"],
            column_config={
                "Becken": st.column_config.SelectboxColumn(
                    "Becken ✏️", options=belegung.becken_auswahl(cfg), required=True, width="medium"),
                "Blöcke": st.column_config.NumberColumn("Blöcke", help="laufende/kommende Kursblöcke"),
            },
        )

        geaendert = bearbeitet[bearbeitet["Becken"] != tabelle["Becken"]]
        if not geaendert.empty:
            st.info(" · ".join(f"**{r.Kurs}** → {r.Becken}" for r in geaendert.itertuples()))

        if st.button("💾 Speichern", type="primary", disabled=geaendert.empty):
            manuell = dict(cfg["becken"].get("manuell") or {})
            for r in bearbeitet.itertuples():
                if r.Becken == belegung.becken_nach_regel(r.Kurs, cfg):
                    manuell.pop(r.Kurs, None)      # entspricht wieder der Regel -> Eintrag unnötig
                else:
                    manuell[r.Kurs] = r.Becken
            belegung.save_manuell(manuell)
            for k in ("becken_editor_True", "becken_editor_False"):
                st.session_state.pop(k, None)   # Bearbeitungen sind jetzt gespeichert
            st.toast(f"{len(geaendert)} Zuordnung(en) gespeichert", icon="✅")
            st.rerun()

        manuell = cfg["becken"].get("manuell") or {}
        if manuell:
            st.caption(f"{len(manuell)} Kurs(e) von Hand zugeordnet (gespeichert in becken_manuell.yaml). "
                       "Zum Zurücksetzen einfach wieder „33m“ auswählen und speichern.")
