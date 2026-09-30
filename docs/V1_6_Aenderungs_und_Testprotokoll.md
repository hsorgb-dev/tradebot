# V1.6 – Backtest, Historical Data Layer und Research-Export: Änderungs- und Testprotokoll

**Basis:** `US_Aktien_Bot_V1_5_Dual_IEX_SIP_Cockpit.ipynb`. Die Datei bleibt unverändert.
**Neu:** `US_Aktien_Bot_V1_6_1_Backtest_Research_Cockpit.ipynb`, erzeugt mit `tools/build_v16.py`.
**Vorab-Analyse:** `docs/V1_6_Analyse_Backtest_und_Datenschicht.md`.

Umgesetzt sind in einem Paket drei Aufträge:

- Backtest-Modus mit Cockpit
- Historical Data Layer
- Research-Export

Strategie-, Risiko- und Scannerparameter sind **unverändert**:

- v02/v03 bleiben byteidentisch (Hash-Prüfung gegen den Scannerlauf).
- Es gibt keine Optimierung, keine Parameter-Sweeps und kein Auto-Tuning.

## A. Versionsstand

V1.6 auf Basis von V1.5. Die RVOL-Regel „18 von 20“ gilt unverändert, sie war bereits in V1.5 freigegeben.

| Modul | Rolle | Änderung |
|---|---|---|
| `bot_mode_v16.py` | **neu:** genau ein Hauptmodus (OBSERVE / DRY_RUN / PAPER / BACKTEST), Zuordnung zur V1.5-Orchestrierung, Order-Sperre | – |
| `orb_sim_v16.py` | Strategie-Schleife | nur Protokoll: abgelehnte Kurs-Crossings (`candidate_rejections.json`), Portfolio-Kontext am Signal, Equity je Minute (`equity_curve.json`); Order-Hook nur im Modus PAPER |
| `orb_replay_v16.py` | Replay | eine Tagesschleife `replay_days` mit Kapitalfortschreibung und Stopp-Hooks; `ReplayBroker` mit Tages-Equity |
| `orb_cockpit_v16.py` | Anzeige | Anzeigemodus `backtest`, Startkapital des Tages statt fest 10.000 |
| `paper_orders_v16.py` | Paper-Order-Gateway | jede Orderprüfung (`_guard`) verlangt zuerst den Modus PAPER |
| `hist_data_v16.py` | **neu:** Historical Data Layer | – |
| `data_access_v16.py` | **neu:** DATENZUGANG PRÜFEN | – |
| `backtest_v16.py` | **neu:** Backtest-Ablauf, Datenqualitätsregeln | – |
| `research_export_v16.py` | **neu:** Research-Paket | – |
| `control_panel_v16.py` | **neu:** Cockpit-Bedienfeld (ipywidgets) | – |
| `sip_shadow_bot_v16.py`, `iex_paper_bot_v16.py`, `dual_bot_v16.py` | Live-Bots | nur Importnamen und Versionstext |
| `us_orb_test_v02.py`, `us_orb_scanner_v03.py`, `orb_portfolio_v15.py`, `bot_accounts_v15.py` | unverändert | – |

## B. Historische Datenquelle

Alpaca Market Data API v2 (historical) über `alpaca-py` `StockHistoricalDataClient`:

| Daten | Endpunkt | Einstellungen |
|---|---|---|
| Minutenkerzen | `get_stock_bars` | `TimeFrame.Minute`, `feed=sip`, `adjustment=raw`; mehrere Symbole je Anfrage (50) |
| Tageskerzen (Liquiditätsfilter) | `get_stock_bars` | `TimeFrame.Day`, `feed=sip`, `adjustment=raw` |
| Quotes | `get_stock_quotes` | `feed=sip`; das angefragte Zeitfenster wird ohne `limit` geladen |
| Trades | `get_stock_trades` | `feed=sip` |

- **Börsenkalender und Aktienliste:** über einen Nur-Lese-Proxy des `TradingClient` (`ReadOnlyBroker`). Jeder andere Zugriff wirft `PermissionError`.
- **Pagination:** Das SDK folgt `next_page_token` bis zum Ende (`alpaca/common/rest.py` `_get_marketdata`). V1.6 setzt bei Kerzen, Quotes und Trades kein `limit`.

## C. Feed

- **Backtests:** ausschließlich SIP. Ein anderer Feed wird bei der Konfiguration abgelehnt.
- **Kein Ersatz:** Bei HTTP 403 erscheint `HISTORICAL DATA WARNING … No automatic IEX fallback performed` und der Lauf stoppt mit Status `SIP_UNAVAILABLE`. Filter werden nie stillschweigend abgeschaltet.
- **Live-Feeds unverändert:** BOT 1 IEX, BOT 2 SIP verzögert.
- **Praktische SIP-Verfügbarkeit:** noch **nicht geprüft**. Die Sitzung, in der V1.6 gebaut wurde, erreicht `data.alpaca.markets` nicht und hat keine Schlüssel. Ergebnis liefert der Knopf **DATENZUGANG PRÜFEN** im Notebook; er speichert es unter `data/historical/capabilities/`.

## D. Historische Reichweite

Laut Alpaca-Dokumentation reicht SIP bis 2016 zurück, **praktisch nicht getestet**. DATENZUGANG PRÜFEN testet je Datentyp die Tage 2016-01-04, 2018, 2020, 2022 und 2024 und nennt den frühesten mit Daten. Im Code gibt es kein künstliches Zeitlimit, nur die Untergrenze 2016 bei der Datumseingabe.

## E. Datentypen

| Stufe | Inhalt | Verwendung |
|---|---|---|
| Level 1 | 1-Min-Bars | immer |
| Level 2 | Quotes | nur an den Entscheidungspunkten der Strategie: Signal, frische Einstiegs-Quote, Bid bei Lücken und Glattstellung |
| Level 3 | Trades | nur für ungeklärte RVOL-Vergleichsminuten (Odd-Lot-Prüfung) |

Vollständige Trades der Signal-Aktien und -Tage werden nur mit dem Häkchen „FULL_MARKET_DATA“ geladen. Ganze Quote-Ströme werden nie geladen.

## F. Backtest Quality Level

- `BAR_PLUS_QUOTES` ist die Standardstufe: Die Quote-Filter werden wie live mit historischen SIP-Quotes reproduziert.
- `FULL_MARKET_DATA` gilt nur mit dem Trades-Häkchen.
- `BAR_ONLY` kommt für diese Strategie nicht vor: Der Spread-Filter braucht Quotes. Ohne Quote-Zugang stoppt der Lauf, statt ohne Filter zu rechnen.
- Die Stufe steht in `run_manifest.json`, `summary.json` und jeder Zeile von `trades`/`signals`.

## G. Cache

Ablage: `MyDrive/US_Aktien_Bot/data/historical/`

- `historical_data_manifest.json`: je Partition Symbol, Feed, Datentyp, Timeframe, abgedeckte Sitzungen bzw. Zeitfenster, Start, Ende, Zeilen, Datei, SHA-256, Download-Zeit, Zeitzone (UTC), Quelle und Validierungsbefunde.
- `sip/bars_1min/<SYMBOL>/<JJJJ-MM>.parquet`, `sip/bars_1day/<SYMBOL>/<JJJJ>.parquet`
- `sip/quotes/<SYMBOL>/<JJJJ-MM>.parquet`, `sip/trades/<SYMBOL>/<JJJJ-MM>.parquet`
- `calendar/<JJJJ>.json`, `universe/universe_snapshot.csv`, `logs/download_log.jsonl`, `capabilities/*.json`

**Cache-first:**

- Eine Sitzung gilt als gespeichert, sobald sie fehlerfrei nach Schluss + 15 Min. geladen wurde.
- Geladen werden nur fehlende Paare (Symbol, Sitzung), gebündelt je zusammenhängendem Zeitraum.
- Eine Partition, deren Datei fehlt, beschädigt ist, deren Prüfsumme oder Zeilenzahl abweicht oder die nicht im Manifest steht, gilt als nicht gespeichert.

**Alt-Cache `replay_cache_sip/`:** wird einmalig übernommen.

- Kalender und Aktienliste werden kopiert; dadurch bleibt dasselbe Universum wie bei früheren Replays erhalten.
- Minutentage werden nur übernommen, wenn die Datei nach Schluss + 15 Min. geschrieben wurde und QQQ bis kurz vor Schluss Kerzen hat.
- Abgelehnte Tage werden bei Bedarf neu geladen.
- Alte Quote-, Trade- und Tagesdateien sind über Abfrage-Hashes abgelegt und werden nicht übernommen. Sie sind klein und werden neu geladen.

## H. Datenformat

- **Parquet (pyarrow):** Cache und Research-Tabellen.
- **CSV:** nur die Research-Tabellen trades, signals, daily und equity_curve für manuelle Nutzung.
- **JSON:** Manifest, Summary, Schema.

## I. Warmup

- **Umfang:** `WARMUP_TRADING_DAYS = Config.rvol_days` (20). Aus demselben Wert leitet die Engine RVOL-Vergleichstage und Liquiditätsfilter ab.
- **Laden:** Der Loader lädt die 20 Handelstage vor dem Start mit.
- **Keine Wirkung auf das Ergebnis:** Warmup-Tage werden nie simuliert und erscheinen nicht in `daily`.
- **Dokumentation:** `warmup_start` steht im Manifest.

## J. Research-Output je Lauf

Ablage: `MyDrive/US_Aktien_Bot/backtests/<RUN_ID>/`, Lauf-ID `BT_<YYYYMMDDTHHMMSSZ>_<8 hex>`.

| Datei | Inhalt |
|---|---|
| `run_manifest.json` | Zeitraum, Warmup, Startkapital, Universum, Datenquelle/Feed, Qualitätsstufe, Strategie-, Risiko- und Ausführungsparameter, aktive Filter, Modul-Hashes, Paketversionen, Tagesliste |
| `summary.json` | performance, trades, signals, exits, risk, data_quality |
| `schema.json` | jede Spalte mit Datentyp, Einheit, Beschreibung und Quelle; Definitionen, Session-Buckets, Reject-Codes |
| `trades.*` | eine Zeile je Trade |
| `signals.*` | **jedes** Kandidatensignal inkl. Kurs-/Datenablehnungen, mit Stufe, Grund, Filter-Flags, Kontext und Counterfactuals |
| `daily.*` | eine Zeile je Handelstag, auch nicht simulierte, mit Datenabdeckung |
| `equity_curve.*` | Equity je Entscheidungsminute, Drawdown |
| `events.parquet` | Signal, Annahme/Ablehnung, Einstieg, Stop-Nachzug, Ausstieg, Tagesabschluss, Risikosperre, Datenlücke |
| `data_gaps.parquet` | jede fehlende Minute (Symbol, Datum, Minute NY) |
| `<RUN_ID>_ANALYSIS.zip` | Manifest, Summary, Schema, CSVs, README |

Index aller Läufe: `backtests/backtest_runs.csv`. Kein Lauf wird überschrieben.

## K. Schema (wichtige Spalten)

- **RVOL:** Volumen seit Öffnung bis zur Entscheidung / Ø desselben Fensters der gültigen Vergleichstage (≥ 18 von 20).
- **spread_bps:** (Ask − Bid) / Mid × 10 000.
- **MFE / MAE:** Stück × (höchstes Hoch bzw. min(tiefstes Tief, Ausstieg) − Einstieg). `_pct` ist relativ zum Positionswert.
- **risk_pct:** geplantes Anfangsrisiko / Equity vor dem Einstieg.
- **breakout_distance_pct:** Signalkurs / OR-Hoch − 1.
- **future_return_{1,5,15,30,60}m, future_max_return/drawdown_{15,30}m, …_to_close:** COUNTERFACTUAL, siehe Absatz unten.
- **reject_stage:** PRICE → DATA → TIMING → RVOL → QUOTE → RANKING → ENTRY.
- **reject_reason:** bestehende Engine-Codes.
- **data_reject_code:** DATA_INCOMPLETE, OR_INCOMPLETE, RVOL_REFERENCE_INCOMPLETE, QUOTE_DATA_MISSING, TRADE_DATA_MISSING.
- **failed_\*:** `true` = abgelehnt, `false` = geprüft und bestanden, `null` = nicht geprüft. Die Engine bricht beim ersten Fehlschlag ab. Weitere Prüfungen würden über die simulierte Abfragezeit die Ergebnisse verändern.
- **session_bucket:** OPENING 09:30–10:00, MORNING 10:00–11:30, MIDDAY 11:30–14:00, AFTERNOON 14:00–15:30, LATE_SESSION 15:30–16:00 (NY). Reine Research-Metadaten.
- **Datentypen:** Prozentwerte als Dezimalzahl, Zeitstempel ISO-8601 mit Zeitzone, Booleans `true`/`false`, fehlende Werte leer bzw. null.

**Schutz der Counterfactuals:**

- Sie werden erst nach dem Lauf in `research_export_v16` berechnet.
- Kein Strategie-, Risiko- oder Ausführungsmodul importiert dieses Modul; ein AST-Test prüft das.
- Der Export verweigert Tage ohne Abschlussstatus.

## L. Beispiel-Run (synthetischer Testmarkt, `tests/test_backtest_v16.py`)

Zeitraum 12.–21.09.2026:

- **Kalender:** Wochenende und der Testfeiertag 16.09. werden übersprungen, der 17.09. schließt um 13:00. Es werden 5 Handelstage simuliert.
- **Warmup:** 20 Tage.
- **Ergebnis:** Startkapital 25.000 USD, Status `COMPLETED`.
  - 1 Trade: AAA, Trailing-Stop, 74 Stück aus der 30-%-Grenze.
  - 2 Signale: AAA angenommen, CCC mit `VOLUME_REJECT`; bei CCC gilt `failed_rvol = true`, und der Spread ist nicht geprüft (`null`).
- **Wiederholung:** Ein zweiter Lauf über denselben Zeitraum stellt keine einzige API-Anfrage; alle Tage sind `REUSED`.
- **Gegenprobe:** Derselbe Einzeltag reproduziert den V1.5-Replay-Trade (Stück, Einstieg, Ausstieg, Zeit, PnL).

## M. Analysis Bundle

`MyDrive/US_Aktien_Bot/backtests/<RUN_ID>/<RUN_ID>_ANALYSIS.zip`. Es wird automatisch nach jedem Lauf erzeugt. Der Cockpit-Knopf „ANALYSIS-BUNDLE“ öffnet in Colab den Download.

## N. Datenlimits und bekannte Einschränkungen

- **Quotes/Trades historisch:** Verfügbarkeit hängt vom Konto ab. Die Prüfung erfolgt per DATENZUGANG PRÜFEN; ohne Quote-Zugang stoppt der Backtest.
- **Rate Limit:** Kostenloser Zugang 200 Anfragen/min. V1.6 wartet 0,35 s zwischen Anfragen. Bei 429, 5xx oder Verbindungsfehlern gibt es höchstens 5 Versuche mit Backoff 2/5/15/30 s (das SDK wiederholt 429 zusätzlich intern bis zu 3×). Danach bricht der Lauf ab; alles steht in `logs/download_log.jsonl`.
- **Intrabar-Ambiguität:** Die Reihenfolge von Hoch und Tief innerhalb einer Minute ist unbekannt. Stop-Kerzen mit neuem Hoch sind `intrabar_ambiguous`.
- **Fehlende Minuten:** Sie bedeuten „kein Handel“ und werden nie interpoliert. Sie stehen in `data_gaps` und `missing_bar_count`; die bestehenden OR-, RVOL- und Datenregeln lehnen betroffene Signale ab.
- **Datenqualitätsregeln:**
  - a) Kein Handelstag: kein Fehler.
  - b) Einzelne Minute: nur das betroffene Signal wird abgelehnt.
  - c) Symbol ohne Daten: nur an diesem Tag ausgeschlossen.
  - d) Systemisch – Download fehlgeschlagen, keine QQQ-Kerzen oder weniger als `systemic_min_symbol_share` = 50 % des Universums mit Kerzen: Tag nicht simulieren, Lauf stoppen, Status `INCOMPLETE_DATA`, Ergebnisse speichern. Die 50 % sind eine dokumentierte Einstellung in `BacktestSettings` und stehen im Manifest. Für die Datenabdeckung selbst gibt es keinen festen Grenzwert, sie wird nur erfasst.
  - e) Jeder Kalendertag des Zeitraums steht in `daily`.
- **Survivorship-Bias:** aktueller Nasdaq-100-Stand für alle Tage.
- **Kerzen-Korrekturen:** Historische Kerzen können spätere Korrekturen enthalten.
- **Kosten:** Kommission ist nicht modelliert (0.0); Kosten nur über das 10-bp-Stressszenario.
- **Colab:** Die ipywidgets-Bedienung und der Download-Dialog sind nur offline mit ipywidgets 8 getestet. Das Verhalten in Colab ist ungeprüft.

## O. Bedienung

1. Notebook öffnen, „Laufzeit → Alle ausführen“ (Secrets freigeben, Drive verbinden).
2. Im Cockpit einmal **DATENZUGANG PRÜFEN**. Es zeigt SIP Bars/Quotes/Trades, die Reichweite und den möglichen Level.
3. **BACKTEST** wählen, Start- und Enddatum sowie Startkapital eingeben. Optional **CACHE PRÜFEN**, um zu sehen, was noch fehlt.
4. **▶ START BACKTEST**. Fortschritt und Cockpit erscheinen darunter; Download-Fortschritt und die Tage werden angezeigt.
5. Danach **ERGEBNIS / TRADES / SIGNALE / TAGE**, dann **ANALYSIS-BUNDLE** für die externe Auswertung.
6. Live-Modi:
   - **OBSERVE:** Modus wählen und starten.
   - **DRY RUN:** Modus wählen und starten, optional mit SIP-Vergleichsbot.
   - **PAPER:** zusätzlich die Tagesfreigabe `PAPER-ORDERS-OK <Konto> <Datum>` eintragen.

## Tests

Offline mit pytest unter pandas 3.0, pyarrow 25 und ipywidgets 8.

| Auftrag | Test | Ergebnis |
|---|---|---|
| Test 1 SIP Capability | `test_data_access_v16` (5): Bars/Quotes/Trades getrennt; 403, 429, 401, 422, leer und Pagination unterschieden; Historie; keine Zugangsdaten in Ausgabe und Datei | bestanden (simulierte API) |
| Test 2 Cache | `test_second_request_is_served_from_cache`, `test_second_run_uses_cache_and_completed_days` | bestanden |
| Test 3 Partial Cache | `test_partial_cache_downloads_only_missing_sessions_without_duplicates` | bestanden |
| Test 4 Ein Handelstag | `test_same_strategy_as_the_v15_replay` (Einzeltag, gleiche Trades wie V1.5) und Research-Dateien | bestanden |
| Test 5 Mehrtageszeitraum | Modul-Fixture `test_backtest_v16` (5 Handelstage, Feiertag, verkürzter Tag, Carry-Forward, Warmup, Index) | bestanden |
| Test 6 Rejected Signals | `test_rejected_candidate_signals_are_written`, `test_symbol_without_data_is_excluded_for_that_day_only` | bestanden |
| Test 7 Look-Ahead-Schutz | `test_counterfactuals_are_research_only` (AST und Text), `test_export_refuses_unfinished_days`; Replay-Datenzugriff wie bisher `test_replay_data_never_returns_future_data` | bestanden |
| Test 8 SIP Failure | `test_access_denied_is_a_clear_sip_warning_without_iex`, `test_sip_quotes_forbidden_stops_without_iex` | bestanden |
| Datenpolitik | `test_systemic_data_problem_stops_and_keeps_results`, `test_empty_batch_is_a_data_problem_not_a_permission`, `test_missing_minutes_are_reported_not_filled`, Migration, beschädigte Partition, unfertige Sitzung, Rate Limit | bestanden |
| Regression Modi | `test_modes_v16` (12): OBSERVE = V1.5 SIP_ONLY, DRY RUN ohne Orders, PAPER mit Orders, PAPER + SIP-Vergleichsbot (echter Spawn-Prozess, BOT 2 ohne Orders), Order-Sperre im Gateway und in der Schleife | bestanden |
| Cockpit | `test_control_panel_v16` (6): exklusive Moduswahl, Felder je Modus, Validierung, Backtest per Knopf, Ergebnis-Knöpfe | bestanden |
| Alte Suiten V1.2–V1.5 | unverändert | bestanden |
