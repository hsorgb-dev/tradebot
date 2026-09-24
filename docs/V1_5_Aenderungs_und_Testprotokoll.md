# V1.5 – Dual-Bot-System (IEX + SIP): Änderungs- und Testprotokoll

**Basis:** `US_Aktien_Bot_V1_4_3_SIP_Cockpit.ipynb`. Die Datei bleibt unverändert.
**Neu:** `US_Aktien_Bot_V1_5_Dual_IEX_SIP_Cockpit.ipynb`, erzeugt mit `tools/build_v15.py`.
**Auslieferungszustand:** `BOT_MODE = 'SIP_ONLY'`, `PAPER_ORDER_APPROVAL = None`. BOT 1 ist deaktiviert, es ist keine Alpaca-Orderfunktion erreichbar.

## 1. Bestandsaufnahme V1.4.3

- **Datenfeed:** fest SIP; `fetch_bars` lehnt andere Feeds ab.
- **Orders:** V1.4.3 enthält keine Order-Methoden. Das Portfolio ist rein virtuell mit 10.000 USD, und jeder Tag beginnt wieder bei 10.000 USD.
- **Modi:** `live`, `delayed` und `replay` über `run_dryrun` mit austauschbarer Uhr und austauschbaren Daten- und Broker-Adaptern.
- **Unverändert übernommen:**
  - Hash-geprüfte Strategie und Scanner (`us_orb_test_v02.py`, `us_orb_scanner_v03.py`), byteidentisch
  - Behandlung von Datenlücken
  - keine rückdatierten Stops
  - Cockpit mit Browser-Link

## 1a. Freigegebene Regeländerung (Entscheidung vom 24.09.2026)

**Anlass:** Im verzögerten V1.4.3-Lauf vom 24.09. wurden GILD und LIN mit `NOT_CHECKABLE` abgelehnt, obwohl jeweils 19 von 20 RVOL-Vergleichstagen gültig waren (GILD: 03.09. ungültig, LIN: 28.08. ungültig, jeweils `ValueError`).

**Änderung:** `DryRunSettings.min_rvol_reference_days = 18`. Der RVOL wird aus dem Durchschnitt der gültigen Vergleichstage berechnet, wenn mindestens 18 von 20 gültig sind. Mit dem Wert 20 gilt die alte Regel. Die hash-geprüfte Konfiguration (v02) bleibt unverändert.

**Nachvollziehbarkeit:** Zu jedem ungültigen Vergleichstag steht jetzt der genaue Grund in `reference_checks/*.csv`, Spalte `error_detail`. Das umfasst Uhrzeit und Werte einer widersprüchlichen Kerze sowie die Trade-Bedingungen einer nicht erklärbaren fehlenden Minute.

**Tests:** `tests/test_rvol_reference_v15.py`, 5 Fälle: 19 von 20 gültig ergibt `VOLUME_PASS` mit RVOL über 19 Tage; die alte Regel mit 20 bleibt verfügbar; 17 von 20 bleibt `NOT_CHECKABLE`; Fehlerdetails; Prüfung des Einstellwerts.

## 2. Architektur

| Modul | Rolle | Änderung gegenüber V1.4.3 |
|---|---|---|
| `orb_sim_v15.py` (war `sip_shadow_sim_v14.py`) | gemeinsame Live-Schleife: Scanner, Opening Range, Signale, RVOL, Ranking | Feed `sip`/`iex` als Einstellung; Startkapital als Einstellung; optionaler Order-Hook (nur Echtzeit); Parameter für Ergebnisordner und Cockpit-Modus; `processing_utc` an Signalen; Cockpit-Update auch in Wartephase und nach Glattstellung; Status `…_WITH_PAPER_ORDERS` bei gesendeten Orders |
| `orb_portfolio_v15.py` | Positionsgröße, Risiko, Trailing-Stop, Tagesabschluss (virtuell) | Feed-gebunden; `processing_utc` an jedem Ereignis; MFE/MAE je Trade |
| `orb_cockpit_v15.py` | Cockpit und Browser-Link | Anzeigemodi für BOT 1 und BOT 2 (SIMULIERT); zusätzliche JSON-Kopie; Kennzahlen für den Vergleich |
| `orb_replay_v15.py` | Replay, verzögerte Uhr und Daten | Replay-Speicher lehnt Nicht-SIP-Anfragen ab |
| `bot_accounts_v15.py` | **neu:** virtuelle Konten je Bot, Tagesbuchung genau einmal, CSV-Protokolle | – |
| `sip_shadow_bot_v15.py` | **neu:** BOT 2 mit `VirtualBroker` (ohne Netzwerk, ohne Order-Methoden) und `CatchUpClock` (Nachsimulation ab Öffnung, danach 16 Min. Versatz) | importiert nichts aus `alpaca.trading` |
| `paper_orders_v15.py` | **neu:** Paper-Order-Gateway, nur von BOT 1 geladen | – |
| `iex_paper_bot_v15.py` | **neu:** BOT 1 (IEX-Echtzeit, IEX-Simulation, optional Paper-Orders) | – |
| `dual_bot_v15.py` | **neu:** Modi, Orchestrierung, Drei-Bereiche-Cockpit, Vergleichsprotokoll | BOT 2 läuft im `DUAL_MODE` in eigenem Prozess (`spawn`) |

**Gleiche Aktiengrundgesamtheit:** Beide Bots erhalten dieselbe Nasdaq-100-Liste. Der Liquiditätsfilter über 20 Vortage bleibt für beide historisches SIP.

## 3. Tests (pytest, 106 grün unter pandas 2.2 und 3.0)

| Abnahmekriterium (Auftrag §13) | Test | Ergebnis |
|---|---|---|
| `SIP_ONLY` starten: nur BOT 2, keine Ordermethoden erreichbar | `test_dual_bot_v15::test_sip_only_runs_bot2_and_never_touches_the_trading_client`, `test_sip_shadow_bot_v15::test_bot2_cannot_reach_any_order_method` | bestanden (simuliert) |
| Verzögerter SIP-Abruf nur in zulässigen Fenstern | Test-Markt, der jede Anfrage jünger als 15 Min. ablehnt: `test_sip_shadow_bot_v15` (beide Tagesläufe), `test_delayed_v143` | bestanden, nie jünger als 16 Min. |
| Virtueller Kauf/Verkauf nur intern | `test_full_day_books_the_virtual_account` | bestanden |
| SIP-Neustart: Cursor/Positionen korrekt, keine Doppelbuchung | `test_restart_mid_day_catches_up_chronologically_and_never_books_twice`, `test_incomplete_run_is_not_booked` | bestanden |
| `IEX_ONLY`: Paper-Orders nur nach Freigabe | `test_iex_paper_bot_v15::test_without_approval…`, `…invalid_approval…` (3 Varianten), `…valid_approval…` | bestanden mit simuliertem Paper-Client |
| Konto-/API-Prüfung: nur Paper | `test_live_endpoint_is_refused_even_with_valid_approval` | bestanden |
| `DUAL_MODE`: beide unabhängig, keine Kontamination | `test_dual_mode_runs_both_bots_isolated` (BOT 2 in echtem Spawn-Prozess) | bestanden |
| SIP verzögert/fehlerhaft blockiert IEX nicht | `test_failing_bot2_process_does_not_block_bot1` | bestanden |
| Unterschiedliche Positionen: Cash/Equity/Risiko getrennt | getrennte Konten `IEX_REALTIME_PAPER_SIM` und `SIP_DELAYED_SHADOW` im Dual-Test | bestanden |
| Tagesabschluss beider Bots | Glattstellung und Position 0 (BOT 1), 15:30 virtuell (beide), Status `COMPLETED` | bestanden |
| Vergleichsprotokoll: Zeitzuordnung, keine irreführenden Zwischenvergleiche | Dual-Test (Signalzeit 09:56 bei beiden, `comparison_complete`); Absturz-Test (nie vollständig ohne SIP-Tag); Cockpit „VORLÄUFIG“ | bestanden |
| Fehlende Marktdaten: keine unzulässigen Orders/Fills | V1.4.3-Suite: Lücken, verspätete Kerzen, kein rückdatierter Stop | bestanden |
| Verspäteter Start: kein nachträglicher IEX-Kauf, korrektes SIP-Replay | `test_missed_signal_is_not_ordered_after_a_late_start`, Neustart-Test BOT 2 | bestanden |
| Replay oder verzögerte Daten erzeugen nie Orders | `test_replayed_or_delayed_runs_refuse_an_order_hook` | bestanden |
| Neustart sendet keine zweite Einstiegsorder | `test_restart_never_sends_a_second_entry` | bestanden |

## 4. Offen: ohne echte API bzw. Handelszeit nicht geprüft

- **Kein Lauf gegen echte Alpaca-Server.** Alle Tests laufen mit künstlichen Märkten und einem simulierten Paper-Client.
- **Paper-Orders bei Alpaca ungeprüft:**
  - Annahme von IOC-Limit-Order und `TrailingStopOrderRequest` (`trail_percent`, `DAY`)
  - Verhalten bei Teilausführung
  - `close_all_positions` und Zeitbedarf bis Position 0
  - Filter `symbols` bei `get_orders`
- **IEX-Echtzeit bei Alpaca ungeprüft:** Zugriff mit dem kostenlosen Konto, tatsächliche Kerzenlatenz, IEX-Quotes für den Spread.
- **Colab-spezifisch ungeprüft:**
  - `multiprocessing`-Spawn von BOT 2 im Colab-Kernel
  - Anzeige des Drei-Bereiche-Cockpits, während die Zelle läuft
  - Browser-Link
- **Ratenlimit des kostenlosen Kontos (200/Min.):** Im `DUAL_MODE` fragen beide Bots parallel ab. Im Normalbetrieb sind es grob 10–15 Anfragen pro Minute, dazu kommen Spitzen beim Nachladen der RVOL-Referenzen und bei der Nachsimulation.
- **Colab-Abbruch:** Es gibt keine garantierte Glattstellung. Die Trailing-Stops beim Broker gelten nur für den Tag.
- **Gebühren:** Alpaca-Paper berechnet keine Kommissionen. Kosten fließen nur über das 10-Basispunkte-Stressszenario ein, ohne Doppelzählung mit dem Bid/Ask-Modell.

## 4a. Replay-Variantenvergleich (Stopabstand, Wiedereinstieg)

**Zweck:** Nur zur Auswertung. Die Live-Strategie bleibt unverändert: Trailing 1 %, ein Einstieg je Aktie und Tag. Die Standardwerte `max_reentries=0` und `atr_trailing_multiple=0` entsprechen genau dem bisherigen Verhalten.

**Notebook:** `US_Aktien_Bot_V1_5_Variantenvergleich.ipynb`. Modul `orb_variants_v15.py`, erstellt mit `python3 tools/build_v15.py <module_dir> <cells_dir> <ziel.ipynb>`.

**Ablauf:**
1. Ein normaler SIP-Replay je Tag (`run_replay`) erfasst Signale, Ranking und Einstiegsquotes.
2. Jede Variante simuliert nur das Portfolio mit denselben Minutenkerzen neu. Dafür sind keine weiteren API-Abrufe nötig.

**Varianten:**

| Name | Stop | Wiedereinstieg |
|---|---|---|
| `A_1.0pct` (Basis) | 1,0 % | nein |
| `A_1.5pct` | 1,5 % | nein |
| `A_2.0pct` | 2,0 % | nein |
| `A_ATR` | 0,5 × Tagesspanne (ATR 20 T.), 0,75–3 % | nein |
| `B_1.0pct_reentry` | 1,0 % | max. 1, nur nach Stop, ≥15 Min. Pause, neues vollständig geprüftes Signal |
| `C_1.5pct_reentry`, `C_2.0pct_reentry`, `C_ATR_reentry` | wie A | wie B |

**Positionsgröße:** Bei gleichem Risikobudget verkleinert ein weiterer Stop die Stückzahl. Die 30-%-Kapitalgrenze bleibt bestehen.

**Auswertung:** `comparison.csv` mit drei Zeiträumen:
- **„Festlegen (bis Split)“:** Zeitraum, in dem die Variante ausgewählt wird.
- **„Pruefen (ab Split)“:** unberührter Kontrollzeitraum.
- **„Gesamt“**

Kennzahlen je Variante und Zeitraum: Trades, Trefferquote, Summe, Stressergebnis, Ø R, Profitfaktor, maximaler Drawdown, Stops, Wiedereinstiege.

**Konsistenzprüfung:** Die Basisvariante muss den Replay reproduzieren (`baseline_consistency.csv`, `sweep.json` → `baseline_matches_base_replay`). Ausstiegsminute und Ergebnis müssen übereinstimmen. Die Sekunde darf durch die simulierte Latenz abweichen.

**Tests:** `tests/test_variants_v15.py`, 6 Fälle:
- Basis reproduziert den Replay.
- Ein 2-%-Stop übersteht einen Rücksetzer, den 1 % ausstoppt.
- Wiedereinstieg nach Stop und Pause.
- Ohne Freigabe kein Wiedereinstieg.
- ATR-Stop mit Untergrenze.
- Ausgabedateien.

**Offen:** Noch kein Lauf mit echten Alpaca-Daten.

## 5. Freigabe von BOT 1 (später, gesondert)

1. Einige erfolgreiche Tage im Modus `SIP_ONLY` auswerten.
2. `BOT_MODE = 'IEX_ONLY'` oder `'DUAL_MODE'` setzen und `PAPER_ORDER_APPROVAL = 'PAPER-ORDERS-OK <Paper-Kontonummer> <YYYY-MM-DD>'` für den jeweiligen Tag eintragen. Die Freigabe gilt nur für dieses Datum und dieses Konto.
3. Das Paper-Konto muss beim Start frei von fremden Positionen und Orders sein.
