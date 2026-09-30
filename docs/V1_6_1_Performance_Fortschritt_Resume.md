# V1.6.1 – Backtest: Performance, kompakte Fortschrittsanzeige, Checkpoint und Resume

**Grundsatz:** Intern rechnet die Strategie weiter Minute für Minute; nach außen zeigt das Cockpit nur Tagesstände. Eine Colab-Unterbrechung kostet höchstens den Handelstag, der gerade lief.

**Keine Strategieänderung.** ORB, RVOL, Ausbruchspuffer, Risiko, Trailing Stop, Sektorlimit, Ein- und Ausstiegsregeln sind unverändert. Der Beleg dafür:

- **Messlauf:** Derselbe Backtest mit 5 Tagen und 100 Aktien ergibt vorher und nachher **identische** Research-Tabellen (Abschnitt 2).
- **Neue Äquivalenztests:** 101 Tests zu Signal-Scan und RVOL-Referenzen auf Zufallsdaten mit Lücken, ungültigen und doppelten Kerzen.

## 1. Hauptursachen der Langsamkeit (gemessen)

Messwerkzeug: `tools/profile_backtest_v16.py`. Es erzeugt einen synthetischen SIP-Markt mit 100 Aktien, 5 Handelstagen und 20 Warmup-Tagen, lokal gemessen; Drive-Latenz optional simuliert.

| Ursache | vorher | Anteil |
|---|---|---|
| Signal-Scan: jede Minute wurde für jede Aktie die Minutenreihe seit Öffnung mit pandas neu aufgebaut (164.500 Aufrufe) | 904 s | 54 % |
| RVOL-Prüfung je Signal: 20 Vergleichstage jeweils aus dem ganzen 20-Tage-Rahmen gefiltert und mit pandas geprüft | 488 s | 29 % |
| RVOL-Referenzen: je Aktie und Lauf 20 CSV-Kopien geschrieben und wieder gelesen | 107 s | 6 % |
| Replay-Datenzugriff: jede Minute alle Kerzen des Tages zusammengefügt, gefiltert, sortiert und normalisiert | 107 s | 6 % |
| Datei-Schreiben: 23.501 Schreib- und 6.821 Lesevorgänge in 5 Tagen, z. B. Zustand, Ereignisse und Minuten-Zusammenfassung jede Minute, HTML-Cockpit 250-mal pro Tag | 55 s lokal, auf Drive ein Vielfaches | – |
| Datenphase: Parquet-Zusammenführung je Symbol **und** Sitzung; Alt-Cache-Übernahme schrieb nach jedem Tag alle Partitionen | 59 s | 4 % |

**„Mehrere Minuten passiert nichts“** hatte zwei Gründe:

- Datenphase, Alt-Cache-Übernahme und Tageskerzen liefen ohne Fortschrittsmeldung.
- Das Cockpit meldete sich im Replay nur alle 5 simulierten Minuten mit einem kompletten HTML-Neuaufbau.

## 2. Laufzeiten vorher und nachher

(Nachher-Werte mit gebündelten Tagesdateien; beide Nachher-Läufe liefen parallel auf derselben Maschine.)

| 5 Handelstage × 100 Aktien | vorher (V1.6.0) | nachher (V1.6.1) | Faktor |
|---|---|---|---|
| lokal (ohne Drive-Latenz) | **1.686 s** | **334 s** | **5,1×** |
| mit 40 ms je Dateizugriff (Drive-Annahme) | ≈ 2.900 s (geschätzt: 1.686 s + 30.322 Zugriffe × 40 ms) | **357 s** (gemessen) | ≈ 8× |
| API-Anfragen | 47 | 47 | – |
| Datei-Schreibvorgänge | 23.501 | 478 | – |
| Datei-Lesevorgänge | 6.821 | 82 | – |
| Konsolenzeilen | 45 | 0 | – |
| Cockpit-Neuaufbauten je Tag | 250 (HTML + 3 Dateien) | 1 je 5 s Wanduhr, zusätzlich bei Tageswechsel und Trade | – |

Ergebnis identisch: trades (4), signals (1.780), daily (5), equity_curve (1.735), events (3.670) und data_gaps sind Zeile für Zeile gleich (`assert_frame_equal`). Ausgenommen sind nur die Spalten Lauf-ID und Ordnerpfad.

Die Zahlen gelten für den synthetischen Markt; echte Laufzeiten in Colab hängen von CPU und Drive ab. Der synthetische Markt erzeugt mit ≈ 350 geprüften Signalen je Tag eher mehr Signale als der echte Nasdaq-100.

## 3. Weniger Cockpit-Aktualisierungen

- **Aggregator statt HTML-Cockpit:** Die Strategie-Schleife meldet sich weiter jede Minute, aber nur noch bei `BacktestProgress`. Das speichert ein paar Zahlen, ohne Rendering und ohne Dateizugriff (Test: < 0,2 ms je Aufruf).
- **Eigener Anzeige-Thread:** Er baut die kompakte Ansicht alle 5 s neu auf, bei Trade-Ein- oder -Ausstieg und Tageswechsel spätestens nach 1 s.
- **Tageszeile:** eine Zeile je fertigem Tag, zum Beispiel:

  ```
  2026-09-15 | 99/100 Symbols | 18 Signals | 1 Trades | +8 USD | Equity 10,008 USD | SIMULATED
  ```

- **Keine Minutenausgabe mehr:** kein `print` und kein Widget-Update je Minute. Die Konsolentexte der Tagesschleife sind im Backtest stumm (`DryRunSettings.quiet`).

## 4. Weniger Drive-I/O

Die folgenden Punkte gelten nur im Backtest. Live bleibt alles wie in V1.5, weil die Einstellungen dort `persist_every_minute=True` bleiben.

- **`RunWriter`:** Zustand, Ereignisse und Trades werden zum geplanten Tagesabschluss und am Tagesende geschrieben statt jede Minute.
- **Signal- und RVOL-Prüfdateien gebündelt:** zusammengefasst zu `signals_bundle.json` und `reference_checks_bundle.csv` je Tag.
- **Minuten-Zusammenfassungen:** eine Datei `minute_summaries.json` statt 390 Einzeldateien je Tag.
- **RVOL-Referenzen:** bleiben im Speicher, keine CSV-Kopie je Aktie und Lauf.
- **Cache-Schreiben:** nur wenn sich etwas geändert hat; die Alt-Cache-Übernahme schreibt alle 10 Tage statt nach jedem Tag.

## 5. Geänderte Dateien

| Datei | Änderung |
|---|---|
| `orb_sim_v16.py` | numpy-Signal-Scan (`price_candidates_until`, Referenz `price_candidates_until_slow` für Tests); `ReferenceHistory` mit Schnellweg für RVOL-Vergleichstage (Rückfall auf die Originalprüfung bei jeder Auffälligkeit); `RunWriter`; `DryRunSettings.persist_every_minute / reference_cache_on_disk / quiet`; Replay-Kerzen ohne doppelte Normalisierung |
| `orb_replay_v16.py` | Tageskerzen einmal vorbereitet, pro Minute nur zeitlich geschnitten; Tagesstatus vor dem Tages-Hook |
| `hist_data_v16.py` | eine Parquet-Zusammenführung je Symbol und Partition; Fortschritt für Tageskerzen und Alt-Cache |
| `backtest_v16.py` | Phasen 1–6, `BacktestProgress`, `checkpoint.json` mit Heartbeat, Resume, Pause, Status CREATED/RUNNING/PAUSED/COMPLETED/FAILED/INCOMPLETE_DATA/ABORTED, Auto-Save je Tag |
| `research_export_v16.py` | Research-Teile je Tag (`parts/<Datum>/`), Endexport fügt die Teile zusammen; Signal-Bündel lesen |
| `control_panel_v16.py` | kompakte Laufansicht, Backtest im eigenen Thread, ⏸ PAUSE NACH TAG, INCOMPLETE BACKTEST FOUND mit RESUME/DISCARD, `keep_alive()` |
| Notebook, `tools/profile_backtest_v16.py`, Tests | Bedienhinweise, Messwerkzeug, `test_perf_equivalence_v16.py`, `test_resume_v16.py` |

## 6. Checkpoint-Struktur (`backtests/<RUN_ID>/checkpoint.json`)

| Feld | Inhalt |
|---|---|
| `run_id`, `status`, `status_reason`, `created_at`, `updated_at`, `heartbeat_at` | Lauf und Zustand; das Lebenszeichen `heartbeat_at` wird alle 30 s geschrieben |
| `period`, `days_total`, `settings` | Zeitraum und Backtest-Einstellungen (Startkapital, Feed …) |
| `records`, `last_completed_date` | abgeschlossene Handelstage mit Tagesordner, Start-/End-Equity, Trades |
| `current_equity`, `cash`, `completed_trades` | Stand nach dem letzten fertigen Tag (abends flach, deshalb Cash = Equity) |
| `daily_results` | Tageszeilen fürs Cockpit |
| `research_state` | Tage, deren Research-Teile unter `parts/<Datum>/` liegen |
| `strategy_parameters`, `scanner_parameters`, `dry_run_settings`, `data_feed`, `fingerprint` | Code und Parameter; Resume nur bei identischem Fingerabdruck |

## 7. Resume-Verhalten

- **Beim Öffnen des Cockpits** werden `backtests/*/checkpoint.json` gesucht. Bei Status CREATED, RUNNING oder PAUSED erscheint **INCOMPLETE BACKTEST FOUND** mit Lauf, Zeitraum, x / y Tagen, letztem Tag und Equity. RUNNING ohne Lebenszeichen seit 3 Min. gilt als „unterbrochen“.
- **▶ RESUME BACKTEST** setzt denselben Lauf (dieselbe Lauf-ID) am nächsten Handelstag fort, mit der Equity aus dem Checkpoint. Fertige Tage werden **nicht** neu gerechnet (Test). Hat sich Code oder Parameter geändert, wird Resume abgelehnt.
- **Konsistenz:** Research-Export und Summary nach einem Resume sind identisch mit einem ununterbrochenen Lauf (Test).
- **DISCARD** setzt den Status auf ABORTED; alle Dateien bleiben erhalten.
- **⏸ PAUSE NACH TAG** hält nach dem laufenden Tag an (Status PAUSED); „Ausführung unterbrechen“ in Colab ebenso.

## 8. Bekannte Colab-Grenzen

- **Keine garantierte Hintergrundausführung.** Colab beendet Laufzeiten nach Inaktivität oder Maximaldauer (kostenlos etwa 12 h). Wird der Browser geschlossen, läuft die Laufzeit meist noch eine Weile weiter, garantiert ist das nicht.
- **Hintergrund-Thread und Leerlauf:** Der Backtest läuft in einem eigenen Thread; die Zelle ist danach frei. Für lange Läufe deshalb die Zelle „Laufzeit aktiv halten“ (`app.keep_alive()`) ausführen. Sie hält die Laufzeit beschäftigt, ohne den Lauf zu steuern.
- **Laufzeit verloren:** Dann fehlt höchstens der laufende Handelstag; mit RESUME geht es weiter.
- **Drive-Synchronisierung:** Sie kann hinterherhinken. Der Checkpoint wird atomar geschrieben (`.incomplete` → Umbenennen).
- **Nur offline geprüft:** Widgets aus einem Hintergrund-Thread und der Download-Dialog sind nicht in Colab getestet.

## 9. Bedienung (kurz)

1. „Alle ausführen“. Liegt ein unterbrochener Lauf vor: **▶ RESUME BACKTEST** oder **DISCARD**.
2. **BACKTEST** wählen, Zeitraum und Startkapital eintragen, **▶ START BACKTEST**.
3. Das Cockpit zeigt Phase, Tage x / y, aktuellen Tag, Signale, Trades, Equity, Abdeckung, Laufzeit und „Letzte Aktualisierung“ (alle 5 s), darunter eine Zeile je Tag.
4. Bei langen Läufen die Zelle **„Laufzeit aktiv halten“** ausführen.
5. Anhalten mit **⏸ PAUSE NACH TAG**; später **RESUME**.
6. Danach wie bisher ERGEBNIS / TRADES / SIGNALE / TAGE / ANALYSIS-BUNDLE.

## 10. Tests

| Auftrag | Test | Ergebnis |
|---|---|---|
| Test A – 1 Handelstag | `test_same_strategy_as_the_v15_replay` (Einzeltag, gleiche Trades wie V1.5) | bestanden |
| Test B – 5 Handelstage | Modul-Fixture `test_backtest_v16`, `test_checkpoint_after_every_day` | bestanden |
| Test C – nach 2 Tagen stoppen, RESUME | `test_pause_then_resume_does_not_repeat_days_and_matches`: Tage 1–2 nicht neu gerechnet | bestanden |
| Test D – Unterbrechung | `test_interrupted_runtime_keeps_the_checkpoint` (KeyboardInterrupt mitten in Tag 3), `test_stale_running_status_is_detected_as_interrupted` | bestanden |
| Test E – Research nach Resume = ununterbrochen | in Test C: trades, signals, daily, equity_curve, summary gleich | bestanden |
| Gleiche Entscheidungen | `test_perf_equivalence_v16` (101 Fälle), Messlauf 5 × 100 identisch | bestanden |
| Anzeige | `test_compact_running_view`, `test_quiet_backtest_prints_nothing_per_minute`, `test_no_minute_output_and_day_wise_files`, `test_progress_update_is_cheap` | bestanden |
| Gesamtsuite | alle Versionen V1.2–V1.6.1 | 273 bestanden (pandas 3.0, pyarrow 25, ipywidgets 8) |
