# kickbot

Tippt bei [kicktipp.de](https://www.kicktipp.de) automatisch, falls du bis kurz vor
Anpfiff selbst noch nicht getippt hast. Bereits von dir gesetzte Tipps werden nie
überschrieben.

## Wie es funktioniert

1. Login mit deinen Kicktipp-Zugangsdaten - per einfachem HTTP-POST
   (`requests`), kein Browser involviert.
2. Lädt die Tippabgabe-Seite deiner Gruppe als HTML und liest darin alle
   offenen Spiele samt Anstoßzeit und den dort von Kicktipp angezeigten
   Buchmacher-Quoten (1/X/2) aus (`BeautifulSoup`).
3. Für Spiele, die **noch nicht getippt** sind und **innerhalb von
   `TIP_LEAD_TIME_MINUTES`** anpfeifen: berechnet aus den Quoten einen
   plausiblen Tipp und trägt ihn ins Formular ein.
4. Sendet das komplette Tippformular (alle Spiele, nicht nur die
   geänderten) per HTTP-POST ab - genauso, wie es ein Browser täte.

Die Quoten kommen direkt aus dem HTML der Kicktipp-Seite selbst (kein
externer Odds-API-Key nötig). Login-Formular und Tippabgabe-Seite sind
gewöhnliche serverseitig gerenderte HTML-Formulare ohne JS-Challenge oder
CSRF-Token - deshalb kommt der Bot ganz ohne Browser/Selenium aus. Das
macht ihn tauglich für schwache Hardware wie einen Raspberry Pi: keine
Chromium-Installation, RAM-Bedarf im einstelligen MB-Bereich, ein Lauf
dauert typischerweise unter einer Sekunde CPU-Zeit statt mehrerer Sekunden
für einen Browserstart.

## Setup

```bash
cd /home/user/Projects/kickbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env ausfüllen: KICKTIPP_USERNAME, KICKTIPP_PASSWORD, KICKTIPP_COMMUNITY
# (KICKTIPP_COMMUNITY = der Teil der URL: kicktipp.de/<hier>/tippabgabe)
```

Sonst nichts - kein Chrome/Chromium, kein Chromedriver, keine Systempakete.
`requirements.txt` zieht nur `requests`, `beautifulsoup4` und
`python-dotenv` (reines Python, keine C-Extensions), das reicht auch für
einen Raspberry Pi OS Lite ohne Desktop-Umgebung.

## Manuell testen

```bash
# Zeigt nur an, was getippt würde, ohne etwas abzusenden
python run.py --dry-run --verbose
```

**Wichtig beim ersten Testlauf:** Prüfe im Log, ob für die anstehenden
Spiele Quoten gefunden werden ("Tipped: ..." mit `odds 1/X/2 = ...`) oder
ob "No odds found" für praktisch jedes Spiel auftaucht. Falls Letzteres:
das würde bedeuten, dass die Quoten auf deinem Kicktipp-Account doch erst
per JavaScript nachgeladen werden (z.B. abhängig vom Werbe-/Premium-Status
des Accounts) und nicht im rohen HTML stehen - dann bräuchte es doch wieder
einen Browser oder eine alternative Quotenquelle. Das konnte ich ohne
echten Kicktipp-Login nicht abschließend testen, nur die Login- und
Formular-Mechanik selbst ist gegen die echte Seite verifiziert.

## Automatisch laufen lassen (Cron)

```bash
crontab -e
# Inhalt aus cron/kickbot.cron.example einfügen und Pfade anpassen
```

Der Bot läuft alle 15 Minuten, greift aber nur ein, wenn ein Spiel innerhalb
von `TIP_LEAD_TIME_MINUTES` (Standard: 90 Minuten) anpfeift und noch nicht
getippt wurde. Ein Lockfile (`.kickbot.lock`) verhindert überlappende Läufe.
Logs landen in `logs/kickbot.log` bzw. `logs/cron.out`.

## Konfiguration (.env)

| Variable | Bedeutung |
|---|---|
| `KICKTIPP_USERNAME` / `KICKTIPP_PASSWORD` | Deine Login-Daten |
| `KICKTIPP_COMMUNITY` | Gruppen-Slug aus der URL |
| `TIP_LEAD_TIME_MINUTES` | Wie kurz vor Anpfiff eingegriffen wird (Standard 90) |
| `SKIP_ALREADY_TIPPED` | Nie eigene Tipps überschreiben (Standard true, nicht ändern) |
| `USER_AGENT` | User-Agent für die HTTP-Requests (Standard: normaler Desktop-Chrome) |
| `NTFY_TOPIC` / `NTFY_URL` | Optional: Push-Alarm via [ntfy.sh](https://ntfy.sh) bei Fehlern |
| `WEBHOOK_URL` | Optional: alternativer/zusätzlicher Alarm-Webhook (JSON POST) |
| `TEAM_STATS_LEAGUE` | Optional: `bl1`/`bl2` für Formkurve via OpenLigaDB (leer = aus) |
| `TEAM_STATS_SEASON` | Saison-Jahr für OpenLigaDB (leer = aktuelle Saison) |
| `TEAM_STATS_WEIGHT` | Wie stark Formkurve die Toranzahl verschiebt (Standard 0.35) |
| `TEAM_STATS_LOOKBACK` | Anzahl letzter Spiele pro Team für die Formkurve (Standard 6) |

## Benachrichtigungen bei Fehlern

Ohne Konfiguration landet bei einem Fehler nur ein Log-Eintrag in
`logs/kickbot.log` - das merkt man erst, wenn man zufällig reinschaut.
Trägst du `NTFY_TOPIC` (kostenlos, ohne Anmeldung über
[ntfy.sh](https://ntfy.sh)) oder `WEBHOOK_URL` in `.env` ein, bekommst du
eine Benachrichtigung bei:

- Login fehlgeschlagen
- Tippabgabe-Seite/-Tabelle nicht wie erwartet (deutet auf ein geändertes
  Kicktipp-Layout hin)
- Kicktipp hat abgegebene Tipps abgelehnt
- für ein fälliges Spiel konnten keine Quoten gelesen werden (häufigstes
  Symptom, wenn sich am Quoten-Markup etwas geändert hat)
- jeder sonstige unerwartete Fehler

## Sicherheitshinweis

`.env` enthält dein Kicktipp-Passwort im Klartext und ist per `.gitignore`
von Git ausgeschlossen. Committe diese Datei nie in ein Repository, auch
nicht in ein privates.

## Tipp-Logik

`kickbot/predictor.py` errechnet aus den Quoten (1/X/2) implizite
Gewinnwahrscheinlichkeiten und fittet daraus ein Poisson-Modell (erwartete
Tore für Heim- und Auswärtsteam), dessen Sieg/Unentschieden/Niederlage-
Wahrscheinlichkeiten den Quoten am nächsten kommen. Getippt wird die unter
diesem Modell wahrscheinlichste Torkombination - dadurch unterscheidet der
Bot z.B. einen 60%-Favoriten (typ. 1:0) von einem 85%-Favoriten (typ. 3:0),
statt beide auf denselben Wert zu pauschalisieren. Das ist trotzdem nur ein
Fallback für vergessene Tipps, kein Anspruch auf Treffsicherheit. Bei
K.-o.-Spielen (Verlängerung/Elfmeterschießen) wird kein Unentschieden
getippt, da Kicktipp das dort ablehnt.

### Formkurve (optional)

Die Quoten allein legen zwar fest, wer wie wahrscheinlich gewinnt, aber
nicht, ob es ein knappes 1:0 oder ein torreiches 3:2 wird - zwei Teams mit
identischer Sieg-Quote können völlig unterschiedlich torhungrig/-anfällig
sein (starker Sturm, schwache Abwehr vs. umgekehrt). Ist `TEAM_STATS_LEAGUE`
gesetzt, lädt `kickbot/team_stats.py` die letzten Ergebnisse der Liga von
[OpenLigaDB](https://www.openligadb.de/) (kostenlos, kein API-Key) und
berechnet je Team die durchschnittlich geschossenen/kassierten Tore der
letzten `TEAM_STATS_LOOKBACK` Spiele. Das verschiebt in `predictor.py` nur
die **Gesamttoranzahl** nach oben oder unten - wer als Favorit gilt, bleibt
weiterhin allein von den Quoten bestimmt (siehe `_blend_with_team_stats`).

Team-Namen werden zwischen Kicktipp ("Gladbach") und OpenLigaDB ("Borussia
Mönchengladbach") per Fuzzy-Matching zugeordnet (`match_team_name`). Ist
die Zuordnung nicht eindeutig, ein Team noch zu neu in der Liga oder die
OpenLigaDB-Abfrage nicht erreichbar, fällt der Bot für dieses Spiel
stillschweigend auf reine Quoten-Logik zurück - nie ein harter Fehler.
