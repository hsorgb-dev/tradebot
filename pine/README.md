# MNQ 1-Uhr-Range-Ausbruch (TradingView, Pine Script v6)

`MNQ_1Uhr_Range_Ausbruch.pine` ist eine TradingView-Strategie für **MNQ1! im 5-Minuten-Chart**.
Alle Uhrzeiten sind New Yorker Zeit. Die Sommerzeitumstellung wird automatisch berücksichtigt.

## Regeln

| Schritt | Regel |
|---|---|
| Range | Höchstes Hoch und tiefstes Tief der Kerzen 01:00, 01:05 und 01:10 (01:00–01:15 NY) |
| Long | Erste 5-Min-Kerze, die **über** dem Range-Hoch **schließt** → Kauf zur Eröffnung der nächsten Kerze |
| Short | Erste 5-Min-Kerze, die **unter** dem Range-Tief **schließt** → Verkauf zur Eröffnung der nächsten Kerze |
| Stop | Gegenüberliegende Seite der Range |
| Ziel | 0,5 R ab dem tatsächlichen Ausführungskurs (R = Abstand Einstieg – Stop) |

### Vorgaben, die in der Aufgabe offen waren (alle einstellbar)

- **Ausbruchskerze:** standardmäßig „Schluss außerhalb“. Bei „Ganzer Körper außerhalb“ müssen Eröffnung und Schluss beide jenseits der Range liegen.
- **Ein Trade pro Tag.** Ist `Max. Trades pro Tag` größer als 1, braucht jeder weitere Trade einen neuen Ausbruch: Der Kurs schließt erst wieder innerhalb der Range, dann erneut außerhalb.
- **Letzter Einstieg 12:00 NY, Glattstellen um 15:55 NY.**
- **Kosten:** 0,62 $ Kommission je Kontrakt und Seite, 1 Tick Slippage bei Market- und Stop-Orders. Ein Ziel gilt erst als erreicht, wenn der Kurs 1 Tick darüber hinaus handelt.
- **Margin-Prüfung aus** (`margin_long/short = 0`), damit TradingView ein kleines Konto nicht nach dem Aktien-Margin-Modell liquidiert.

Ein Ziel von 0,5 R bei 1 R Stop braucht **vor Kosten eine Trefferquote über 66,7 %**, um die Gewinnschwelle zu erreichen. Bei engen Ranges fressen die Kosten einen großen Teil jedes Gewinns.

## Einrichtung

1. Pine-Editor öffnen → Datei einfügen → **Zum Chart hinzufügen**, auf `MNQ1!` im 5-Minuten-Zeitrahmen.
2. Strategietester → Übersicht / Liste der Trades.
3. Empfohlen:
   - **Bar Magnifier** (Eigenschaften, ab Premium): Stop und Ziel liegen oft in derselben 5-Min-Kerze.
   - **Rückwärtsanpassung** (engl. *Back-adjustment*) bei Kontraktwechseln in den Symboleinstellungen des Charts: Sie hält den Tages-EMA von Filter 1 sauber.
4. Historie: TradingView lädt je nach Abo nur eine begrenzte Zahl von 5-Min-Kerzen. Mit **Deep Backtesting** lässt sich mehr Historie testen, sonst sind es nur wenige Monate.

## Die fünf Regime-Filter

Alle Filter sind standardmäßig **aus**. Jeder hat eine eigene Eingabegruppe mit dem Kästchen `Aktivieren` und Min/Max-Grenzen.

| # | Regime | Messwert | Standardregel | Getestete Idee |
|---|---|---|---|---|
| 1 | **Trendrichtung** | Vortagesschluss gegenüber Tages-EMA(20) | Darüber nur Longs, darunter nur Shorts | Ausbrüche in Richtung des übergeordneten Trends laufen öfter weiter |
| 2 | **Volatilität (VIX)** | Vortagesschluss von `CBOE:VIX` | 0 – 20 | Ruhige Märkte liefern sauberere Ausbrüche. Mit 20 – 100 das Gegenteil testen. `CBOE:VXN` ist die Nasdaq-100-Variante |
| 3 | **Range-Breite** | Heutige 1-Uhr-Range ÷ Durchschnitt der 1-Uhr-Ranges der letzten 10 Tage | 0,5 – 1,5 | Lässt sehr enge Ranges (Kosten und Rauschen) und sehr weite Ranges (Stop zu weit weg) aus |
| 4 | **Trendstärke (ADX)** | ADX(14) der letzten abgeschlossenen 60-Min-Kerze, im Moment des Ausbruchs | 20 – 100 | Trendmärkte laufen weiter; Seitwärtsmärkte (0 – 20 testen) laufen zum Stop zurück |
| 5 | **Beteiligung über Nacht** | Volumen 18:00 → 01:15 ÷ Durchschnitt der letzten 10 Tage | 1,0 – 100 | Überdurchschnittliche Aktivität in der Nacht bedeutet echtes Interesse hinter der Bewegung |

Kein Filter repaintet. Werte aus höheren Zeitrahmen stammen nur von abgeschlossenen Kerzen. VIX, Range-Breite und Nachtvolumen werden für den Tag festgehalten, sobald die Range um 01:15 fertig ist.

### Regime-Filter-Tabelle (unten rechts im Chart)

Für jeden Filter teilt die Tabelle die **Trades des aktuellen Laufs** auf: in die, die der Filter durchgelassen hätte (*Durchgelassen*), und die, die er blockiert hätte (*Blockiert*). Jede Seite zeigt Anzahl der Trades, Trefferquote und durchschnittliches R nach Kosten. Die Spalte *Heute* zeigt den heutigen Zustand und Wert.

Der Vergleich ist nur in einem Lauf mit **allen Filtern aus** fair. Graue ✕ markieren Ausbrüche, die ein eingeschalteter Filter blockiert hat.

## Den besten Filter finden

1. **Basislauf:** alle Filter aus. Nettogewinn, Profitfaktor, Trefferquote, maximalen Drawdown und Anzahl der Trades notieren.
2. **Vorauswahl:** In der Tabelle nach Filtern suchen, bei denen Ø R unter *Durchgelassen* deutlich über Ø R unter *Blockiert* liegt, mit jeweils mindestens etwa 30 Trades auf beiden Seiten.
3. **Bestätigen:** Jeweils einen Filter einschalten und seine Strategietester-Ergebnisse mit dem Basislauf vergleichen. Auf einen blockierten ersten Ausbruch kann später ein zweiter folgen, deshalb können diese Zahlen leicht von der Tabelle abweichen.
4. **Robustheit:** Jede Schwelle leicht verschieben (zum Beispiel ADX 15 / 20 / 25). Ein Vorteil, der nur bei einem exakten Wert besteht, ist Zufall.
5. **Out-of-Sample:** Mit `Backtest-Zeitraum` Filter und Schwellen auf den ersten ~70 % der Historie auswählen und dann ohne Änderung auf den letzten ~30 % prüfen.
6. Die besten zwei Filter erst kombinieren, wenn jeder einzeln besteht.

## Automatisierung (Webhooks)

Jede Order trägt eine JSON-`alert_message`. Feldnamen und Werte bleiben englisch, weil Webhook-Brücken sie so erwarten:

```json
{"symbol":"MNQ1!","action":"buy","qty":1,"price":25010.25,"stop":24990.00,"target":25020.25}
{"symbol":"MNQ1!","action":"exit","reason":"tp_or_sl"}
{"symbol":"MNQ1!","action":"exit","reason":"eod"}
```

`tp_or_sl` heißt Ziel oder Stop erreicht, `eod` heißt Glattstellen zum Tagesende.

1. Alarm auf der Strategie anlegen → *Nur Order-Ausführungen* (engl. *Order fills only*) → Nachricht `{{strategy.order.alert_message}}`.
2. Die Webhook-URL der Brücke im Alarm eintragen. Feldnamen an das anpassen, was die Brücke erwartet (TradersPost, PickMyTrade usw.).
3. `Symbol in Alarm-Nachrichten` ersetzt `MNQ1!`, zum Beispiel durch den Frontmonat-Kontrakt, den der Broker braucht.

Es gibt zwei Betriebsarten:

- **TradingView steuert die Ausstiege:** Jede Ausstiegs-Ausführung sendet `exit`.
- **Der Broker hält eine Bracket-Order:** Stop und Ziel mit dem Einstieg senden und nur den `eod`-Ausstieg weiterleiten.

In der zweiten Betriebsart basiert das Ziel in der Einstiegsnachricht auf dem Schlusskurs der Signalkerze, nicht auf dem Ausführungskurs. Es kann deshalb ein bis zwei Ticks vom Backtest abweichen.

Zuerst auf einem Papier- oder Simulationskonto laufen lassen.
