# V1.6 – Ist-Analyse und Plan: Backtest-Modus, Historical Data Layer, Research-Export

Stand 29.09.2026. Analyse vor der Umsetzung. **Umgesetzt:** siehe `docs/V1_6_Aenderungs_und_Testprotokoll.md` (Entscheidungen des Nutzers: V1.6 auf V1.5, SIP-Test über das Cockpit, Modi mit Zusatzschalter „SIP-Vergleichsbot parallel“, Kapitalfortschreibung, Datenqualitätsregeln a–e).
Basis ist `US_Aktien_Bot_V1_5_Dual_IEX_SIP_Cockpit.ipynb` (Entscheidung des Nutzers).

## 0. Ausgangslage

1. **Der vorige Schritt ist noch nicht umgesetzt.** Der Auftrag „Historical Data Layer“ setzt voraus, dass es Backtest-Modus, Mode Selector, Datumsauswahl und Backtest-Ergebnisse im Cockpit schon gibt. In V1.5 existiert davon nichts:
   - Modus-Schalter ist die Code-Zeile `BOT_MODE = 'SIP_ONLY' | 'IEX_ONLY' | 'DUAL_MODE'`.
   - Replays laufen nur im Notebook V1.4.3 oder im Variantenvergleich.
   - **Plan:** Beide Schritte zusammen als V1.6 umsetzen, V1.5 bleibt unverändert.
2. **Den SIP-Praxistest kann ich aus dieser Sitzung nicht ausführen.**
   - Der Proxy der Sitzung sperrt `data.alpaca.markets` (CONNECT 403).
   - Es sind keine Alpaca-Schlüssel hinterlegt; sie liegen als Colab-Secrets vor.
   - **Plan:** Der Test wird eine Cockpit-Funktion („DATENZUGANG PRÜFEN“), die im Colab-Notebook mit dem echten Konto läuft. Offline wird er mit simulierten API-Antworten getestet: 403, 429, leer, Pagination, ungültige Anfrage.
3. **Testbasis:** Testsuite lokal 105/106 grün. Der eine Fehlschlag entsteht nur, weil IPython in der Testumgebung fehlte.

## 1. Ist-Zustand der Datenschicht (V1.5)

| Thema | Ist in V1.5 | Fundstelle |
|---|---|---|
| Alpaca SDK | `alpaca-py` (ungepinnt, `%pip install alpaca-py`; lokal geprüft mit 0.44.0) | Notebook Zelle 1 |
| Clients | `TradingClient(paper=True, url_override=paper-api)`, `StockHistoricalDataClient` | Notebook Zelle 16 |
| IEX-Nutzung | nur Live: BOT 1 `DryRunSettings.feed='iex'` → `DataFeed.IEX` | `iex_paper_bot_v15.run_iex_day`, `orb_sim_v15.data_feed` |
| SIP-Nutzung | BOT 2 (verzögert), Replay, Liquiditäts-Vorfilter (für alle Bots historisches SIP) | `orb_sim_v15`, `orb_replay_v15` |
| Historical Bars | Minute RAW 09:30–16:00 NY je Tag für das ganze Universum + QQQ; Tageskerzen RAW für den Liquiditätsfilter | `ReplayStore.day_minutes`, `ReplayStore.daily` |
| Historical Quotes | punktuell: „letzte Quote im 10-s-Fenster vor t“ (`limit=1`, `sort=DESC`) | `inspect_signal`, `fresh_quote`, `exit_quotes` |
| Historical Trades | nur zur Odd-Lot-Klärung fehlender RVOL-Referenzminuten | `missing_odd_lot_volume` |
| Caching | `MyDrive/US_Aktien_Bot/replay_cache_sip/`, siehe unten | `ReplayStore` |
| Drive-Struktur | `v0_3_runs/`, `shadow_replay_v1_4/` bzw. `_v1_5/<Datum>/<Lauf-ID>/`, `…/summaries/<Lauf-ID>/`, `v1_5/<BOT>/…`, `replay_cache_sip/`, `cockpit_latest.html` | – |
| Replay Data Loader | `ReplayData` (liefert nie Zukunftsdaten), `ReplayBroker` (keine Ordermethoden) | `orb_replay_v15` |
| Backtest Data Loader | kein eigener; der Replay ist der Backtest | – |
| Rate Limit | `ReplayStore`: 0,35 s Pause je Quellabfrage (≈170/min); SDK wiederholt 429/504 bis 3× mit 3 s Pause. Kein eigenes Backoff, keine Protokollierung der Wiederholungen | `ReplayStore._call`, `alpaca/common/rest.py` |
| Pagination | übernimmt das SDK vollständig über `next_page_token` (`_get_marketdata`, 10.000 Zeilen je Seite); `limit` gilt als Gesamtlimit | `alpaca/common/rest.py` |
| Multi-Symbol | Bars: 50 Symbole je Anfrage (Replay), 20 (Live). Quotes/Trades: je Symbol | `ReplayStore`, `fetch_bars` |
| Market Calendar | Alpaca `get_calendar`, je Jahr gecacht; verkürzte Tage mit echter Schlusszeit | `ReplayStore.calendar` |
| Zeitzonen | intern UTC tz-aware, Kalender NY → UTC, Anzeige NY | alle Module |
| Validierung | beim Lesen durch die Strategie: `clean_bars` (OHLC, Duplikate), `checked_window`, Quote-Prüfungen. **Beim Download und im Cache keine Prüfung** | `orb_portfolio_v15.clean_bars` |
| Logs | je Tag: `manifest.json` (Modul-Hashes, Config, Fingerabdruck), `status.json`, `coverage.json`, `shadow_events.json`, `shadow_trades.json`, `signals/*.json`, `reference_checks/*.csv` | `orb_sim_v15.run_dryrun` |
| Backtest-Outputs | `summaries/<Lauf-ID>/summary.json`, `days.csv`, `trades.csv` | `orb_replay_v15.summarize` |

### Die zehn Fragen

1. **Welche Daten nutzt der Backtest?**
   - SIP-Minutenkerzen des Tages und der 20 Vortage für das ganze Universum + QQQ.
   - SIP-Tageskerzen der 20 Vortage (Liquidität).
   - SIP-Quotes punktuell an Signal-, Einstiegs- und Ausstiegszeitpunkten.
   - SIP-Trades nur für fehlende Referenzminuten.
   - Alpaca-Kalender und ein einmal gespeicherter Nasdaq-100-Stand.
2. **Feed:** SIP.
3. **Explizit gesetzt?** Ja. Im Replay-Speicher steht überall `feed=DataFeed.SIP`, und `ReplayData` lehnt jede Nicht-SIP-Anfrage ab. Live setzt `data_feed(settings.feed)`: BOT 1 IEX, BOT 2 SIP.
4. **Neu geladen?** Nein. Beim zweiten Lauf kommt alles aus dem Drive-Cache, bereits fertige Tage werden über den Fingerabdruck sogar ganz übersprungen.
5. **Cache?** Ja, aber unstrukturiert und ohne Metadaten:
   - `minute/<Datum>.csv.gz`: ein Tag, alle Symbole, plus `<Datum>.symbols.json`
   - `daily/<hash>.json`, `quotes/<Datum>/<hash>.json`, `trades/<Datum>/<hash>.json`: eine Datei je Abfrage
   - `calendar/<Jahr>.json`, `universe_snapshot.csv`
   - Es fehlen Manifest, Download-Zeit, Zeilenzahl, Prüfsumme, Validierung und Vollständigkeitsvermerk.
   - **Risiko:** Ein Tag, der beim ersten Abruf unvollständig war, bleibt unvollständig im Cache.
   - **Doppelung:** Zusätzlich kopiert jeder Tageslauf RVOL-Referenzen nach `<Laufordner>/sip_reference_minutes/`.
6. **Manifest / Laufordner?** Ja, je Tag ein Laufordner mit `manifest.json`. Lauf-ID-Format `YYYYMMDDTHHMMSSZ_<8 hex>`. Ein Manifest für den ganzen Backtest-Lauf und ein Daten-Manifest gibt es nicht.
7. **RVOL-Historie:**
   - `prepare_reference` holt beim ersten relevanten Signal eines Symbols die 20 Vortage (im Replay aus dem vorgewärmten Speicher).
   - `check_sip_rvol` vergleicht das Volumen von Öffnung bis Signal mit demselben Zeitfenster jedes Vortags.
   - Fehlende Referenzminuten werden, wenn möglich, über SIP-Trades mit ausschließlich Odd-Lot-Bedingungen (`@`,`I`) erklärt; höchstens 40 Lücken je Fenster.
   - Gültig ab 18 von 20 Tagen.
8. **Fehlende Minuten:**
   - Sie gelten als „kein beobachteter Handel“ (NaN, Volumen 0), es wird nie interpoliert.
   - Opening Range: mindestens 12 von 15 Kerzen, die erste ist Pflicht.
   - Ausbruch: echte Kerze in der Signalminute, vorheriger echter Schluss höchstens 5 Min. alt, Abdeckung ≥ 85 %.
   - Gehaltene Position: Die Minute bleibt bis 120 s ausstehend und wird dann als unbeobachtet markiert, geprüft über die frische Bid. Nach mehr als 3 Minuten in Folge ohne Kerze folgt der Ausstieg zur Bid.
9. **Welche Quotes braucht der Filter?** Nur die letzte SIP-Quote vor wenigen Zeitpunkten:
   - Signalminute: Alter ≤ 2 s, Spread ≤ 0,10 %, Ask ≥ 20 USD, Schwelle < Ask ≤ Obergrenze
   - Einstieg: frische Quote zum Verarbeitungszeitpunkt, Kauf zum Ask
   - Lückenprüfung und Glattstellung: frische Bid
   - Keine vollständigen Quote-Ströme.
10. **API-Limits** (laut Alpaca-Dokumentation, **praktisch noch nicht geprüft**):
    - Kostenloser Zugang: 200 Anfragen/min, SIP-Historie ohne die letzten 15 Minuten, Historie ab 2016.
    - Größere Abrufe laufen über 10.000 Zeilen je Seite.

## 2. Was V1.5 für den Backtest-Schritt schon mitbringt

Gegenüber der V1.4.3-Analyse vom selben Tag:

- **Schon vorhanden:**
  - `DryRunSettings.initial_cash` (Startkapital)
  - MFE/MAE je Trade
  - Ablehnung von Order-Hooks bei Replay/Delayed in `run_dryrun`
  - fortgeschriebene Konten (`BotLedger.start_equity`)
  - `processing_utc` an Ereignissen
- **Weiterhin fehlend:**
  - Bedienfeld und Datumsauswahl
  - Kapitalfortschreibung im Replay (`run_replay` beginnt jeden Tag mit `initial_cash`, `ReplayBroker` meldet fest 10.000)
  - Datumsprüfung
  - technische Order-Sperre im Datenlader (`ReplayStore` hält den vollen `TradingClient`)
  - Backtest-Bericht
- **Strategie** wie V1.4.3, einzige freigegebene Änderung: RVOL ab 18 von 20 Tagen.
  - Einstiege 09:46 bis vor 15:15 NY, Glattstellung 15:30 NY.
  - Das wirksame Risiko je Position liegt wegen der 30-%-Kapitalgrenze bei ≈ 0,3 %.

## 3. Wesentliche Entwurfsentscheidungen

1. **Keine zweite Engine, kein zweiter Replay.**
   - Die neue Datenschicht `HistoricalDataStore` bekommt dieselbe Schnittstelle wie `ReplayStore`: `sessions`, `calendar`, `universe`, `day_minutes`, `daily`, `quotes`, `trades`.
   - `ReplayData`, `ReplayBroker` und `run_dryrun` bleiben unverändert.
   - Der Variantenvergleich funktioniert weiter.
2. **Getrennte Feeds.**
   - Der historische Feed ist Teil der Backtest-Einstellung (`BACKTEST_FEED`, Standard `SIP`) und gilt für einen ganzen Lauf.
   - Die Live-Logik (IEX/SIP je Bot) wird nicht angefasst.
   - Fehlt SIP: Warnung „HISTORICAL DATA WARNING … No automatic IEX fallback performed“ und Abbruch. IEX nur bei bewusster Wahl, dann ist es als Feed des ganzen Laufs im Manifest dokumentiert.
3. **Speicher** unter `MyDrive/US_Aktien_Bot/data/historical/<feed>/<datentyp>/<SYMBOL>/<JJJJ-MM>.parquet`:
   - Parquet über pyarrow, das in Colab vorinstalliert ist. CSV nur für kleine Exporte.
   - `historical_data_manifest.json` enthält je Symbol, Feed, Datentyp und Monat: die abgedeckten Sitzungen, Zeilen, Datei, Prüfsumme, Download-Zeit und Quelle.
   - **Abgedeckt** heißt „vollständig abgeschlossene Sitzung geladen“. Eine SIP-Minute ohne Handel ist keine Cache-Lücke.
4. **Cache-first je Sitzung:** Benötigte Sitzungen minus abgedeckte ergibt die fehlenden. Geladen wird nur, was fehlt, gebündelt in Multi-Symbol-Anfragen über zusammenhängende Zeiträume. Danach folgen Validierung und Eintrag ins Manifest.
   - Sitzungen, deren Schluss + 15 Min. noch nicht vorbei sind, werden nie gecacht.
   - **Migration:** Der vorhandene `replay_cache_sip/minute` wird einmalig mit Validierung übernommen, damit bereits geladene Daten nicht erneut heruntergeladen werden.
5. **Quotes (Level 2):** Die Strategie braucht nur die letzte Quote vor wenigen Zeitpunkten, keine ganzen Tage.
   - Die Datenschicht lädt deshalb je Anfrage das ganze Suchfenster (10 s) ohne `limit`.
   - Sie speichert die Rohquotes zusammen mit dem abgedeckten Zeitintervall.
   - Spätere Anfragen innerhalb abgedeckter Intervalle kommen aus dem Cache.
   - So wird der Live-Filter exakt reproduziert, ohne Gigabytes an Quote-Strömen.
6. **Trades (Level 3):**
   - Die Datenschicht stellt SIP-Trades auf Anfrage bereit, gecacht nach derselben Struktur. Genutzt werden sie für die Odd-Lot-Referenzen und später für Research.
   - Ganze Zeiträume werden nie ungefragt geladen.
7. **Data Requirement Resolver:** Er leitet den Bedarf aus der Konfiguration ab.
   - `rvol_days`, `opening_minutes` und Liquiditätsfilter → Bars
   - `maximum_spread`, `maximum_quote_age_seconds` → Quotes
   - Odd-Lot-Referenzen → Trades punktuell
   - **Fehlt Pflichtdatenmaterial, bricht der Lauf ab.** Ein Filter wird nie stillschweigend weggelassen, sonst wäre es eine andere Strategie.
8. **Warmup** = `Config.rvol_days` Handelstage vor dem Start, derselbe Wert steuert den Liquiditätsfilter. Er wird aus der Strategie abgeleitet, nicht festgelegt. Warmup-Tage werden nur geladen, nie simuliert, und fließen nicht in die Performance ein.
9. **Qualitätsstufe je Lauf:**
   - `BAR_ONLY`, `BAR_PLUS_QUOTES` oder `FULL_MARKET_DATA`.
   - Ein Standard-Backtest mit Quotes an allen Entscheidungspunkten wird `BAR_PLUS_QUOTES`.
   - `FULL_MARKET_DATA` erfordert Trades für den ganzen Zeitraum und entsteht nur auf ausdrücklichen Wunsch.
10. **Research-Schicht getrennt von der Strategie.**
    - Eigenes Modul `research_export_v16`. Es liest nur fertige Laufordner und den Cache und wird von keinem Strategie-, Risiko- oder Ausführungsmodul importiert. Ein Test prüft Importe und Quelltext.
    - Kontrafaktische `future_*`-Felder entstehen erst nach Abschluss des Laufs.
11. **Signale vollständig erfassen:** Heute verwirft `price_candidates_until` Ausbrüche über der Preisobergrenze oder unter 20 USD ohne Protokoll.
    - **Plan:** eine reine Protokoll-Erweiterung ohne Verhaltensänderung. Abgesichert wird sie durch einen Äquivalenztest, der gleiche Trades und gleiche Signalstatus verlangt.
    - Die Engine prüft Filter nacheinander und stoppt beim ersten Fehlschlag. `failed_*` ist deshalb `true`/`false` nur für geprüfte Filter und sonst `null`.
    - Zusätzliche Prüfungen einzubauen würde über die simulierte Abfragezeit (0,3 s je Abfrage) die Ergebnisse verändern und ist ausgeschlossen.
12. **Bestehende Reject-Codes** bleiben erhalten, z. B. `VOLUME_REJECT`, `SPREAD_TOO_WIDE`, `QUOTE_STALE`, `ASK_OUTSIDE_ENTRY_BAND`, `DAILY_LOSS_LOCK`, `Sektorlimit erreicht`, `NO_CAPITAL_OR_RISK_BUDGET`. Die booleschen `failed_*`-Spalten werden daraus abgebildet. Die Zuordnung steht in `schema.json`.
13. **Lauf-ID** nach vorhandenem Muster: `BT_<YYYYMMDDTHHMMSSZ>_<8 hex>`, Ordner `MyDrive/US_Aktien_Bot/backtests/<Lauf-ID>/`, dazu der Index `backtest_runs.csv`. Die Tages-Laufordner bleiben bestehen und werden verlinkt.
14. **Marktkontext:**
    - QQQ ist bereits vorhanden.
    - SPY kommt nur als Research-Symbol in die Datenschicht und wird der Strategie nie übergeben.

## 4. Umsetzungsplan (V1.6, V1.5 bleibt unverändert)

| Phase | Inhalt | Tests |
|---|---|---|
| 1 Bedienung | Bedienfeld mit ipywidgets: OBSERVE / DRY RUN / PAPER / BACKTEST, eine Variable `BOT_MODE`, Banner, Datumsfelder, Startkapital, ▶ START; Datumsprüfung; Einzeltag und Mehrtag | Moduszuordnung, Bedienfeld-Logik ohne Colab, Datumsprüfung |
| 2 Sicherheit | Nur-Lese-Proxy statt `TradingClient` im Datenlader (Whitelist `get_calendar`, `get_all_assets`); vorhandene Order-Hook-Sperre | kein `submit_order`/`close_all_positions`/`cancel_orders` erreichbar |
| 3 Datenschicht | `HistoricalDataStore`, Manifest, Parquet-Partitionen, Cache-first, Validierung, Lückenbericht, Warmup, Resolver, Qualitätsstufe, Backoff mit Protokoll, Migration des alten Caches, Fortschrittsanzeige | Tests 1, 2, 3, 8 aus dem Auftrag (API simuliert) |
| 4 Kapital | Fortschreibung über Tage; bei Fehltag Abbruch der Kette mit klarer Meldung | Test 5 |
| 5 Research | `run_manifest.json`, `summary.json`, `schema.json`, `trades`/`signals`/`daily`/`equity_curve`/`events` als Parquet + CSV, `BT_<ID>_ANALYSIS.zip`, `backtest_runs.csv` | Tests 4, 6, 7 |
| 6 Cockpit | Datenstatus, Download-Fortschritt, „DATENZUGANG PRÜFEN“, Ergebnis-Ansicht nach dem Lauf | Darstellungstests |
| 7 Regression | OBSERVE / DRY RUN / PAPER funktional unverändert; gesamte V1.5-Suite; Äquivalenz Backtest ↔ bisheriger Replay | bestehende und neue Tests |

**Nicht hier prüfbar:** die echte Alpaca-Anbindung (SIP-Verfügbarkeit, tatsächliche Reichweite der Historie, Ratenlimit) und das Verhalten der Widgets in Colab. Beides zeigt der Test „DATENZUGANG PRÜFEN“ im Notebook. Der Abschlussbericht nennt diese Werte erst, wenn der Test im Notebook gelaufen ist.

## 5. Offene Entscheidungen

1. **Moduszuordnung auf V1.5:**
   - OBSERVE = `SIP_ONLY` (BOT 2, SIP 16 Min. verzögert, simuliert)
   - DRY RUN = `IEX_ONLY` ohne Freigabe (nur IEX-Simulation)
   - PAPER = `IEX_ONLY` mit Freigabetext `PAPER-ORDERS-OK …` (bleibt als Pflichtfeld im Cockpit)
   - BACKTEST = Replay
   - `DUAL_MODE` passt nicht in einen exklusiven Schalter. Vorschlag: bei DRY RUN/PAPER ein Zusatzhäkchen „SIP-Vergleichsbot parallel“.
2. **SIP-Praxistest:** über die Cockpit-Funktion in Colab (empfohlen) oder in dieser Sitzung. Letzteres erfordert:
   - Freigabe von `data.alpaca.markets` in der Netzwerkeinstellung der Umgebung
   - Schlüssel als Umgebungsvariablen `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_SECRET_KEY`
3. **Fehltag bei Kapitalfortschreibung:** Kette anhalten (empfohlen) oder gekennzeichnet weiterlaufen.
4. **Rest des ersten Auftrags** (ab Abschnitt 13), falls er noch Anforderungen enthält.
