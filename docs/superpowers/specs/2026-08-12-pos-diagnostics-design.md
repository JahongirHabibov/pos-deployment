# KASSIO Diagnose- und Reparatursystem — Design

**Datum:** 2026-08-12
**Status:** Entwurf zur Freigabe
**Repository:** `pos-deployment` (öffentlich)
**Zielsystem:** Ubuntu / Debian POS-Terminals beim Kunden

---

## 1. Zweck

Kunden ohne IT-Kenntnisse sollen die häufigsten Störungen ihres POS-Terminals selbst
erkennen und beheben können, ohne dass ein Techniker anreisen muss.

Der konkrete Auslöser: Nach einem Stromausfall wurden BIOS-Parameter zurückgesetzt, das
System bootete nicht mehr (behoben über „Legacy and UEFI"), und der Bondrucker verliert
seither wiederkehrend seine statische IP-Adresse. Jedes Mal muss ein Techniker vor Ort.

Daraus abgeleitet, aber bewusst breiter: ein Werkzeug, das alle diagnostisch relevanten
Komponenten des Kundengeräts prüft, in Kundensprache erklärt, was nicht stimmt, und für
die sicheren Fälle eine Reparatur anbietet.

## 2. Nicht-Ziele

* Kein Ersatz für Fernwartung. Der Zugriff bleibt lokal auf dem Gerät.
* Keine Überwachung, keine Telemetrie, kein automatischer Versand von Daten.
* Keine Verwaltung des POS-Fachbetriebs (Artikel, Kassenbuch, Bons).
* Kein Update- oder Deployment-Werkzeug. `pos-updater` bleibt allein zuständig.
* Kein Docker-Dienst. Das Werkzeug muss funktionieren, wenn Docker defekt ist.

## 3. Invarianten

Diese drei Eigenschaften sind nicht verhandelbar und werden in jeder Entwurfsentscheidung
und jedem Test geprüft.

### 3.1 Rückwirkungsfreiheit

Das Diagnosesystem darf das laufende POS-System weder beschädigen noch beeinträchtigen.

* **Es schreibt ausschließlich in eigene Pfade.** Die einzigen Ausnahmen sind neue,
  eigene Dateien in Systemverzeichnissen (systemd-Unit, sudoers-Drop-in, Desktop-Eintrag).
  Bestehende Fremddateien werden nie verändert.
* **Es liest die POS-Konfiguration, schreibt sie aber nie.** `.env`,
  `docker-compose.prod.yml`, `manifest.json`, `updater-state/` und `backups/` sind
  ausnahmslos lesend.
* **Es installiert keine Pakete.** Fehlt ein Werkzeug (`smartctl`, `nmcli`, …), meldet die
  betroffene Prüfung „nicht verfügbar". Es gibt keinen Auto-Install-Pfad.
* **Es fasst den Docker-Stack nicht als Ganzes an.** Kein `compose up/down`, kein Pull,
  kein Update. Die einzige Container-Reparatur ist der Neustart eines einzelnen
  `pos-*`-Containers.
* **Es sendet nie Rohdaten an den Drucker.** Erreichbarkeitsprüfung ist ein reiner
  TCP-Verbindungsaufbau ohne Nutzdaten. Ein Testdruck läuft über die offizielle POS-API,
  damit kein Bonfragment und kein hängender Druckerpuffer entstehen kann.
* **Es beansprucht messbar wenig Ressourcen.** Die systemd-Unit begrenzt Speicher, CPU und
  Prozesszahl und läuft mit niedriger Priorität; das POS-System hat immer Vorrang.
* **Es erzeugt keine ungefragte Netzlast.** Der Subnetzscan läuft nur auf Knopfdruck,
  nie im Hintergrund, nie nach Zeitplan.
* **Es belegt keinen fremden Port.** Ist 9120 belegt, bricht die Installation mit einer
  Meldung ab, statt etwas zu verdrängen.

### 3.2 Keine Single-Point-of-Failure-Abhängigkeit

Jeder Teil des Werkzeugs muss ohne jeden anderen Teil nutzbar bleiben.

* Jede Prüfung ist gekapselt, läuft mit eigenem Timeout und **wirft nie**. Ein Fehler
  ergibt ein Ergebnis mit Status `unknown` und einem erklärenden Text; alle übrigen
  Prüfungen laufen unverändert weiter.
* Lässt sich ein Prüfmodul nicht einmal importieren, wird es als nicht verfügbar markiert.
  **Der Dienst startet trotzdem.**
* Das Frontend rendert jede Kachel unabhängig. Ein fehlgeschlagener Endpunkt färbt eine
  Kachel, er leert nie die Seite.
* Es gibt keine Startreihenfolge-Abhängigkeit zu Docker, zum POS-Backend, zum Netzwerk
  oder zum `kassio-power-agent`.

Verbindliche Degradationsmatrix:

| Ausfall | Weiterhin voll nutzbar | Eingeschränkt |
|---|---|---|
| Docker-Daemon tot | System, Netzwerk, Drucker, Dienste, Techniker-Setup, Report | Container-Tab zeigt „Docker nicht erreichbar" mit Startanleitung |
| POS-Backend tot | Alles außer POS-Werten | POS-Sollwert und Testdruck nicht abrufbar; Ist-IP, Ping, Port, Scan laufen weiter |
| Netzwerk tot | System, Container, Dienste, Report | Drucker-Tab meldet Netzausfall als Ursache statt Drucker als Verursacher |
| `expected-config.json` fehlt oder defekt | Alle konfigurationsfreien Prüfungen | Drucker-Sollvergleich entfällt; Techniker-Assistent öffnet automatisch |
| Sprachdatei defekt oder unvollständig | Alles | Fehlende Texte fallen auf Deutsch, dann auf den Schlüsselnamen zurück |
| `smartctl`/`nmcli`/`timedatectl` fehlt | Alles übrige | Betroffene Einzelprüfung meldet „nicht verfügbar" |
| Diagnose-Dienst selbst abgestürzt | — | `Restart=always` startet ihn neu; `install.sh` prüft die Gesundheit nach Installation |

### 3.3 Stabile Fehlerbehandlung und Lokalisierung

* Kein roher Traceback erreicht je die Oberfläche. Er geht nach journald und in den
  Support-Report.
* Jede Meldung im UI beantwortet drei Fragen: **Was ist passiert, was bedeutet das, was
  ist jetzt zu tun.** Ein nackter Exit-Code ist ein Fehler im Entwurf.
* Alle für den Kunden sichtbaren Texte stammen aus Sprachdateien. Technische Details
  (Kommando, Exit-Code, Ausgabe) stehen in einem separaten, aufklappbaren Bereich und
  bleiben bewusst unübersetzt, damit der Support immer denselben Wortlaut sieht.

---

## 4. Architektur

### 4.1 Verzeichnisstruktur

```
diagnostics/
  install.sh                        # sudo ./install.sh   → /opt/kassio-diagnostics
  uninstall.sh                      # rückstandsfreie Entfernung
  kassio-diagnostics.service        # gehärtete systemd-Unit
  kassio-diagnostics.desktop        # Startmenü-/Desktop-Eintrag
  bin/
    diag-helper                     # root-eigen, feste Verb-Tabelle, einziger sudo-Pfad
  kassio_diagnostics/
    __main__.py                     # Einstiegspunkt, Argumente, Signalbehandlung
    server.py                       # ThreadingHTTPServer, Routing, Sicherheitsheader
    auth.py                         # Sudo-Sitzung, Ratenbegrenzung, Audit
    privileged.py                   # Aufrufschicht vor diag-helper
    config.py                       # expected-config: Laden, Prüfen, Schreiben, Sichern
    posapi.py                       # POS-Login, Settings lesen/schreiben, Testdruck
    report.py                       # Support-Report inkl. Schwärzung
    i18n.py                         # Sprachdateien, Fallback-Kette
    runner.py                       # führt Prüfungen isoliert und parallel aus
    vendors.py                      # OUI-Tabelle, Web-UI-Pfade, Anleitungstexte
    checks/
      system.py  network.py  docker.py  devices.py  services.py  pos.py
    actions/
      containers.py  network.py  printer.py  system.py
  locales/
    de.json  en.json  ru.json
  web/
    index.html  app.js  styles.css
  tests/
    test_*.py
```

Kein `pip`, kein `npm`, kein Build-Schritt, keine externen Ressourcen zur Laufzeit —
identisch zur Linie des bestehenden `kiosk-agent/`.

### 4.2 Prüfungs-Kontrakt

Jede Prüfung ist eine Funktion ohne Seiteneffekte, die genau eine Datenstruktur liefert:

```python
@dataclass(frozen=True)
class CheckResult:
    id: str                 # "system.boot_mode"
    group: str              # "system" | "network" | "docker" | "devices" | "services" | "pos"
    status: str             # "ok" | "warn" | "fail" | "unknown" | "unavailable"
    title_key: str          # Sprachschlüssel
    message_key: str        # Sprachschlüssel
    params: dict            # Platzhalterwerte, z. B. {"ist": "192.168.1.87"}
    actual: str | None
    expected: str | None
    actions: list[str]      # IDs anbietbarer Reparaturen
    details: str | None     # technisch, unübersetzt
    duration_ms: int
```

`runner.py` führt Prüfungen in einem Thread-Pool aus, jede mit hartem Timeout (Standard 5 s,
Scan abweichend). Zeitüberschreitung, Ausnahme oder Importfehler werden in ein
`CheckResult` mit Status `unknown` beziehungsweise `unavailable` übersetzt. Der Runner
selbst kann nicht scheitern; er liefert im Extremfall eine Liste aus lauter
Fehlerergebnissen.

Frontend enthält keine Prüflogik. Neue Prüfung heißt: eine Funktion plus drei
Sprachschlüssel — kein Frontend-Eingriff.

### 4.3 Aktions-Kontrakt

```python
@dataclass(frozen=True)
class Action:
    id: str                 # "container.restart"
    needs_sudo: bool
    needs_pos_login: bool
    risk: str               # "low" | "medium" | "high"
    confirm_key: str        # Sprachschlüssel des Bestätigungstextes
    handler: Callable[[Params], ActionResult]
```

Jede Aktion durchläuft dieselbe Kette: Bestätigungsdialog → Berechtigungsprüfung →
Ausführung über `diag-helper` oder die POS-API → Audit-Eintrag → erneute Prüfung des
betroffenen Bereichs, damit der Kunde sofort sieht, ob es geholfen hat.

---

## 5. Sicherheitsmodell

Das Repository ist öffentlich und das Werkzeug läuft dauerhaft auf Kundengeräten. Dieser
Abschnitt ist bewusst ausführlich.

### 5.1 Prozess und Erreichbarkeit

Der Dienst läuft **unprivilegiert** unter dem Administrationsbenutzer, der bei der
Installation `sudo` verwendet hat (`SUDO_USER`) — nicht als root. Das unterscheidet ihn
bewusst vom `kassio-power-agent`, der aus gutem Grund als root läuft.

Er bindet ausschließlich `127.0.0.1:9120`. Die Unit erzwingt das zusätzlich über
`SocketBindAllow=ipv4:tcp:9120` und `SocketBindDeny=any`, sodass der Dienst auch bei einem
Konfigurationsfehler keinen weiteren oder öffentlich erreichbaren Port öffnen kann. Aus dem
LAN ist er nicht erreichbar.

Härtung der Unit, analog zum Power-Agent und darüber hinaus:

```ini
# NoNewPrivileges bewusst NICHT gesetzt — Begründung siehe unten.
PrivateTmp=yes
ProtectSystem=full
ReadWritePaths=-/etc/kassio-diagnostics
ProtectHome=read-only
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
RestrictNamespaces=yes
RestrictRealtime=yes
LockPersonality=yes
SystemCallArchitectures=native
LimitCORE=0
SocketBindAllow=ipv4:tcp:9120
SocketBindDeny=any
MemoryMax=512M
CPUQuota=25%
TasksMax=64
Nice=10
IOSchedulingClass=idle
Restart=always
RestartSec=2
```

`LimitCORE=0` verhindert, dass ein Absturz das kurzzeitig gehaltene Sudo-Passwort in eine
Core-Datei schreibt.

Vier dieser Einstellungen weichen absichtlich vom `kassio-power-agent` ab, weil dessen
Modell (läuft als root, macht keine ausgehenden Verbindungen) hier nicht trägt. Die
Abweichungen sind je einzeln begründet, weil eine blind übernommene Härtung das Werkzeug
funktionsunfähig machen würde:

* **`NoNewPrivileges` bleibt aus.** Die Direktive unterbindet setuid-Binaries — und damit
  `sudo` selbst. Da das gesamte Rechtemodell auf „unprivilegierter Dienst plus
  eng gefasster sudo-Aufruf" beruht, würde sie den Dienst vollständig lahmlegen. Der
  Schutz wird stattdessen von der Verb-Tabelle im root-eigenen Helper getragen, die kein
  frei wählbares Kommando zulässt. Aus demselben Grund funktioniert auch `ping`, das seine
  Rohsocket-Rechte über Datei-Capabilities bezieht.
* **`ProtectSystem=full` plus `ReadWritePaths`.** Sowohl `full` als auch `strict` mounten
  `/etc` schreibgeschützt — auch für den über sudo gestarteten root-Kindprozess, da dieser
  den Namensraum der Unit erbt. Ohne die Ausnahme scheitert jedes Speichern der
  Sollkonfiguration mit „read-only file system". `ReadWritePaths` öffnet ausschließlich
  `/etc/kassio-diagnostics`; der Rest von `/etc` bleibt geschützt.
* **`SocketBindAllow`/`SocketBindDeny` statt `IPAddressDeny=any`.** Die IP-Filter des
  Power-Agents wirken auf **alle** Sockets der Unit und würden Subnetzscan,
  Drucker-Erreichbarkeitsprüfung, POS-API-Zugriff und Internetprüfung blockieren. Die
  Socket-Bind-Direktiven beschränken dagegen nur die **lauschenden** Sockets — genau das
  gewünschte Ziel — und lassen ausgehende Verbindungen unberührt. Der Schutz vor Zugriff
  aus dem LAN kommt weiterhin aus der Bindung an `127.0.0.1`.
* **`AF_NETLINK` ist zugelassen und `MemoryDenyWriteExecute` fehlt.** `ip` spricht über
  Netlink mit dem Kernel; ohne diese Adressfamilie liefert jede Netzwerkprüfung leere
  Ergebnisse. `MemoryDenyWriteExecute` würde auf fremde Kindprozesse wirken, deren
  Speicherverhalten wir nicht garantieren können.

`MemoryMax` gilt für die gesamte Kontrollgruppe einschließlich aller Kindprozesse
(`docker`, `ip`, `journalctl`) und ist deshalb auf 512 MB statt 256 MB angesetzt.

### 5.2 Privilegierte Ausführung

Es gibt genau einen Weg zu Root-Rechten: `/opt/kassio-diagnostics/bin/diag-helper`,
Eigentümer `root:root`, Modus `0755`, für den Dienstbenutzer nicht schreibbar.

Der Helper besitzt eine **fest kodierte Verb-Tabelle**. Er akzeptiert nie einen Pfad, nie
ein Kommando und nie eine Option vom Aufrufer, sondern ausschließlich ein Verb und streng
validierte Bezeichner. Kein `shell=True`, jedes Kommando als Argumentliste, jedes mit
Timeout.

Lesende Verben:

| Verb | Argumente | Validierung |
|---|---|---|
| `read system` | — | — |
| `read smart` | Gerät | `^/dev/(sd[a-z]|nvme\d+n\d+)$` |
| `read journal` | Unit | `^[A-Za-z0-9@._-]{1,64}\.(service|timer)$` |
| `read containers` | — | — |
| `read container-logs` | Name, Zeilen | `^pos-[a-z0-9-]{1,32}$`, Zeilen ∈ {50, 200, 1000} |
| `read container-inspect` | Name | wie oben |
| `read network` | — | — |
| `read services` | — | — |
| `read usb` | — | — |
| `read timesync` | — | — |

Verändernde Verben: `act restart-container`, `act restart-network`, `act renew-dhcp`,
`act sync-time`, `act flush-dns`, `act prune-dangling-images`, `act restart-power-agent`,
`act reboot`, `act poweroff`, `act write-config`.

#### Bindende Validierungsregeln

Weil das Repositorium öffentlich ist, kennt ein Angreifer diese Muster im Wortlaut. Die
folgenden Regeln sind deshalb nicht Stilfragen, sondern Teil der Sicherheitsgrenze:

* **Immer `re.fullmatch` mit `\A…\Z`, nie `re.match` mit `$`.** In Python matcht `$` auch
  unmittelbar vor einem abschließenden Zeilenumbruch. `pos-backend\n` käme durch
  `^pos-[a-z0-9-]{1,32}$` — genau die Art Lücke, nach der in öffentlichem Code zuerst
  gesucht wird.
* **Argument-Injection ist die eigentliche Gefahr, nicht Shell-Injection.** Es wird nirgends
  eine Shell benutzt, aber ein Bezeichner mit führendem Bindestrich würde vom aufgerufenen
  Programm als Option gelesen. Jedes Kommando trennt daher Optionen von Werten mit `--`,
  zusätzlich zur Regex, die den Bindestrich am Anfang ohnehin ausschließt.
* **Prüfen, dann verwenden — nie umgekehrt.** Der validierte Wert wird weitergereicht, nicht
  die Eingabe. Kein Bezeichner wird nach der Prüfung noch zusammengesetzt, normalisiert oder
  ergänzt.
* **Unbekanntes Verb, falsche Argumentzahl oder fehlgeschlagene Validierung** führen zu
  Abbruch mit Rückgabewert 2 und einem Protokolleintrag — nie zu einem Ausführungsversuch
  mit Ersatzwerten.

### 5.3 Warum ein sudoers-Drop-in nötig ist

`installer.py` führt Docker durchgehend über `sudo -S` mit gepipetem Passwort aus (u. a.
Zeilen 1688, 1739, 1860). Der Administrationsbenutzer ist damit **nicht zwingend** in der
Gruppe `docker`. Ohne Gegenmaßnahme wäre ausgerechnet der Container-Tab — der am
häufigsten gebrauchte — passwortpflichtig.

Die naheliegende Lösung, den Benutzer in die Gruppe `docker` aufzunehmen, wird **bewusst
verworfen**: Gruppenmitgliedschaft in `docker` ist passwortlos root-äquivalent für jeden
Prozess dieses Benutzers. Das ist eine viel breitere Rechteerweiterung als nötig.

Stattdessen ein eng gefasstes Drop-in, ausschließlich für die lesenden Verben:

```
# /etc/sudoers.d/kassio-diagnostics   root:root 0440
Cmnd_Alias KASSIO_DIAG_READ = /opt/kassio-diagnostics/bin/diag-helper read *
<admin> ALL=(root) NOPASSWD: KASSIO_DIAG_READ
```

Verändernde Verben stehen absichtlich **nicht** darin und verlangen daher das Passwort
über den regulären sudo-Pfad. Die eigentliche Sicherheitsgrenze ist nicht sudo, sondern
die Verb-Tabelle im Helper: Selbst wenn im Webdienst eine Einschleusungslücke existierte,
ließe sich damit nur eine Lesefunktion mit validierten Argumenten auslösen.

`install.sh` schreibt die Datei zunächst temporär, prüft sie mit `visudo -c -f` und
aktiviert sie erst danach atomar. Scheitert die Prüfung, wird nichts installiert.

### 5.4 Umgang mit dem Sudo-Passwort

* Übergabe an `sudo -S -k`. Das `-k` verhindert, dass sudos eigener Zeitstempel-Cache
  gefüllt wird — die Sitzungsgültigkeit existiert nur innerhalb unseres Prozesses und
  weitet sudo für andere Prozesse desselben Benutzers **nicht** auf.
* Gültigkeit 5 Minuten, zusätzlich **2 Minuten Leerlauf-Timeout**, dazu ein sichtbarer
  Knopf „Sperren". Damit liegt das Passwort typischerweise Sekunden statt Minuten im
  Speicher, ohne dass der Kunde bei einer Reparaturserie mehrfach tippt.
* Es wird nie auf Platte geschrieben, nie protokolliert, nie an den Browser gegeben und
  nie in den Report aufgenommen. Nach Ablauf wird der Puffer überschrieben.
* Fünf Fehlversuche führen zu 15 Minuten Sperre. Sperre und jeder Versuch werden
  protokolliert.

### 5.5 Browser-Seite

* Das Sitzungstoken reist in einem eigenen Header (`X-Kassio-Diag-Session`), **nie** als
  Cookie. Damit ist Cross-Site-Request-Forgery strukturell ausgeschlossen: Ein fremder
  Tab kann den Header nicht setzen, ohne dass wir seinen Preflight beantworten.
* Zusätzlich Origin-Allowlist auf `http://localhost:9120` und `http://127.0.0.1:9120`.
* Antwortheader: `Content-Security-Policy: default-src 'self'; img-src 'self' data:;
  frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, dazu
  `X-Content-Type-Options: nosniff` und `Referrer-Policy: no-referrer`.
* Im Frontend wird **kein** `innerHTML` mit Daten befüllt. Container-Logs, Scan-Ergebnisse
  und Gerätenamen sind fremdbeeinflussbar und landen ausschließlich über `textContent`.
* Statische Dateien werden aus einem festen Verzeichnis mit normalisiertem Pfad
  ausgeliefert; Pfad-Traversal wird vor dem Öffnen abgewiesen.

### 5.6 POS-Zugangsdaten

Lesen und Schreiben der POS-Druckereinstellung läuft ausschließlich über die offizielle
API (`POST /api/v1/auth/login`, `GET|PUT /api/v1/settings/system/...`). Die Anmeldung
erfolgt in einem eigenen, freundlich gestalteten Dialog; das Token bleibt im
Arbeitsspeicher des Dienstes, wird nie gespeichert und nie in den Report geschrieben.
Zugangsdaten werden **nicht** in `expected-config.json` hinterlegt.

### 5.7 Netzscan

Nur auf Knopfdruck. Begrenzt auf das `/24` der primären Schnittstelle. TCP-Verbindungstest
auf 9100, 631 und 80 mit 300 ms Timeout, parallelisiert mit fester Obergrenze. Keine
Nutzdaten, keine Schreibzugriffe, kein automatischer Wiederholungslauf.

Zusätzlich ratenbegrenzt: höchstens ein Scan gleichzeitig und höchstens einer alle
30 Sekunden. Ohne diese Bremse wäre der unauthentifizierte Endpunkt ein Hebel, um Netz und
Drucker durch wiederholte Scans lahmzulegen.

### 5.8 Lokale Zugriffskontrolle über die Peer-UID

Eine Bindung an `127.0.0.1` schützt vor dem Netz, **nicht vor anderen Benutzern desselben
Rechners**. Ein TCP-Socket auf der Loopback-Adresse ist für jedes lokale Konto erreichbar.
Ohne Gegenmaßnahme käme ein Nebenkonto oder ein kompromittierter lokaler Dienst über die
lesenden Endpunkte an Container-Logs und den vollständigen Support-Report — beides kann
Geschäftsdaten enthalten.

Deshalb prüft der Dienst bei **jeder** Anfrage die Benutzerkennung der Gegenstelle:

1. Aus dem akzeptierten Socket werden lokale und entfernte Adresse samt Port gelesen.
2. In `/proc/net/tcp` und `/proc/net/tcp6` wird die passende Zeile gesucht und daraus die
   UID des besitzenden Prozesses entnommen.
3. Zugelassen sind ausschließlich der Dienstbenutzer und `root`. Jede andere UID erhält
   `403` und einen Protokolleintrag.
4. Lässt sich die UID nicht bestimmen — etwa weil die Verbindung bereits geschlossen ist —
   wird abgewiesen. Im Zweifel Ablehnung, nie Zulassung.

Reine Standardbibliothek, unter Linux verlässlich, rund dreißig Zeilen. Die Prüfung liegt
vor der Weiterleitung, damit sie kein Endpunkt versehentlich umgehen kann, und sie ersetzt
keine der übrigen Maßnahmen, sondern ergänzt sie.

---

## 6. Berührte Pfade

Vollständige Liste — alles andere ist tabu.

**Neu angelegt (schreibend):**

| Pfad | Eigentümer | Zweck |
|---|---|---|
| `/opt/kassio-diagnostics/` | root:root | Programmdateien |
| `/etc/kassio-diagnostics/expected-config.json` | root:root 0644 | Sollzustand |
| `/etc/kassio-diagnostics/expected-config.json.bak-<ts>` | root:root 0644 | Versionierte Sicherung |
| `/etc/systemd/system/kassio-diagnostics.service` | root:root | Dienst |
| `/etc/sudoers.d/kassio-diagnostics` | root:root 0440 | Enge NOPASSWD-Leseregel |
| `/usr/share/applications/kassio-diagnostics.desktop` | root:root | Startmenü |
| `/etc/chromium/policies/managed/kassio-diagnostics.json` | root:root | Lesezeichen, eigene Datei |
| `/etc/opt/chrome/policies/managed/kassio-diagnostics.json` | root:root | dito |

Firefox verwendet eine **gemeinsame** `policies.json`. Sie wird nur angelegt, wenn sie
fehlt. Existiert sie bereits, wird sie **nicht** verändert; die Installation gibt
stattdessen einen Hinweis aus. Ein fehlgeschlagenes Lesezeichen ist nie fatal.

**Nur lesend:** `.env` (ausschließlich die Schlüssel `POS_PUBLIC_PORT`, `TZ`,
`COMPOSE_PROJECT_NAME`, `HOST_COMPOSE_PROJECT_DIR` — als feste Allowlist im Code),
`docker-compose.prod.yml`, `manifest.json`, `updater-state/state.json`,
`updater-state/upgrades.jsonl`, `backups/`, `/etc/machine-id`, `/sys`, `/proc`, journald.

**Nie berührt:** Datenbank, Docker-Volumes, Images, `manifest.json.sig`, `remote-access.conf`,
alles unterhalb von `pos-*`-Containern.

---

## 7. Sollzustand und Techniker-Assistent

### 7.1 Schema

`/etc/kassio-diagnostics/expected-config.json`, versioniert:

```json
{
  "schema_version": 1,
  "site": {
    "name": "Filiale Musterstadt",
    "technician": "M. Muster",
    "configured_at": "2026-08-12T10:00:00+02:00",
    "language": "de"
  },
  "network": {
    "interface": "enp3s0",
    "subnet": "192.168.1.0/24",
    "gateway": "192.168.1.1",
    "addressing": "static"
  },
  "identity": {
    "machine_id_hash": "sha256:…"
  },
  "devices": [
    {
      "id": "receipt-1",
      "name": "Bondrucker Kasse 1",
      "role": "receipt_printer",
      "ip": "192.168.1.50",
      "mac": "00:26:ab:12:34:56",
      "port": 9100,
      "vendor": "epson",
      "model": "TM-m30III",
      "notes": "Anleitung Seite 4, Netzwerkmenü über Statustaste"
    }
  ],
  "containers": [
    "pos-database", "pos-redis", "pos-backend",
    "pos-frontend", "pos-image-service", "pos-updater", "pos-backup"
  ]
}
```

Rollen: `receipt_printer`, `kitchen_printer`, `label_printer`, `payment_terminal`,
`scale`, `other`. Beliebig viele Geräte.

### 7.2 Assistent

Fehlt oder bricht die Datei, öffnet sich der Assistent automatisch — die übrigen
Prüfungen laufen währenddessen weiter.

Der Assistent führt Feld für Feld und erklärt zu jedem Feld ausführlich, **wofür** der Wert
gebraucht wird und **was passiert, wenn er falsch ist**. Ein Netzscan schlägt gefundene
Geräte als Werte vor, sodass IP und MAC per Klick übernommen werden können statt abgetippt.

Plausibilitätsprüfungen vor dem Speichern, jeweils mit Klartextbegründung:

* IP liegt im angegebenen Subnetz
* MAC syntaktisch gültig und im Netz auffindbar
* keine doppelte IP oder MAC zwischen Geräten
* Gerät gerade erreichbar (Warnung, kein Fehler — Drucker können aus sein)
* Gateway erreichbar
* alle genannten Container existieren

Danach eine Zusammenfassung zum Gegenlesen. Beim Speichern wird die Vorgängerversion mit
Zeitstempel gesichert, damit ein Fehlgriff rückholbar ist. Der Zugang zum
Techniker-Bereich verlangt das Sudo-Passwort.

`machine_id_hash` wird beim ersten Setup aus `/etc/machine-id` gebildet und danach bei
jedem Lauf verglichen.

---

## 8. Tabs und Prüfungen

Sechs Tabs. Die Übersicht ist der Startbildschirm des Kunden; alles Weitere ist Detail.

### Tab 1 — Übersicht

Eine Ampel je Bereich, ein Knopf „Alles prüfen", ein Knopf „Support-Report erstellen".
Ganz oben, unabhängig vom Bereichsraster, drei Kacheln mit der höchsten Praxisrelevanz:
**Uhrzeit**, **Drucker**, **POS-Dienste**.

Die Uhrzeit steht deshalb oben, weil eine nach einem BIOS-Reset verstellte Echtzeituhr drei
scheinbar unabhängige Symptome erzeugt — TLS-Fehler, scheiternde `docker pull`, fehlende
Lizenzprüfung — und der Kunde sonst drei Fehlermeldungen ohne erkennbaren Zusammenhang sieht.

### Tab 2 — Gerät & System

Betriebssystem und Kernel, Betriebszeit, Arbeitsspeicher, CPU-Last, CPU-Temperatur,
Datenträgerbelegung je Einhängepunkt, SMART-Status (falls `smartctl` vorhanden),
Zeitsynchronisation und Abweichung, Zeitzone, **Boot-Modus UEFI oder Legacy**, Secure-Boot-
Status, unsaubere Abschaltungen der letzten Starts, **Stabilität der `machine-id`**.

Boot-Modus und Secure Boot sind rein informativ — reparieren lässt sich das nur im BIOS.
Der Wert liegt darin, dass eine Abweichung nach einem Vorfall sofort sichtbar ist statt
nach zwei Stunden Fehlersuche.

### Tab 3 — Netzwerk

Schnittstellen und Verbindungsstatus, IP, Gateway, DNS, statisch oder DHCP,
Gateway-Erreichbarkeit, Internet-Erreichbarkeit, DNS-Auflösung, ARP-Tabelle,
Subnetzscan auf Knopfdruck.

### Tab 4 — Drucker & Geräte

Eine Karte je Gerät aus `expected-config`, plus Peripherie: USB-Geräte (Scanner,
Kassenlade, EC-Terminal), CUPS-Status, USB-Druckerknoten. Details in Abschnitt 9.

### Tab 5 — POS-Container

Eine Karte je `pos-*`-Container mit Status, Health, Neustartzähler und Image-Version. Klick
öffnet ein **eigenes Log-Fenster**: 50/200/1000 Zeilen wählbar (Voreinstellung 200),
Auto-Aktualisierung abschaltbar, Knopf **„In Zwischenablage kopieren"**, Knopf
**„Als Datei speichern"**.

Zusätzlich in diesem Tab, weil es dort gesucht wird: Zustand von `pos-updater` und
`pos-backup` aus `updater-state/state.json` und `upgrades.jsonl` — insbesondere das Flag
`upgrade_recovery_required`. Steht es auf `true`, hängt das System in einem halb
aktualisierten Zustand; das ist eine Fehlerursache, die man ohne Hinweis lange sucht.
Dazu Versionsabweichungen zwischen Diensten, `download.status` und das Alter der jüngsten
Sicherung in `backups/`.

Eine Aktion zum Aufräumen von Container-Logs entfällt bewusst: `docker-compose.prod.yml`
begrenzt die Logs bereits über `max-size: 30m` und `max-file: 5` je Container.

### Tab 6 — Techniker-Setup

Sudo-geschützt. Anzeige und Bearbeitung von `expected-config.json`, Assistent,
Sicherungsversionen, Diagnose des Werkzeugs selbst (Version, Port, Dienststatus,
sudoers-Regel vorhanden, Helper-Rechte korrekt).

---

## 9. Drucker-Diagnoseablauf

Der Kern des Systems, entlang des realen Fehlerbilds:

1. **Sollwert** aus `expected-config` lesen.
2. **Erreichbarkeit prüfen:** maßgeblich ist der TCP-Verbindungsaufbau auf den
   konfigurierten Port, ohne Nutzdaten — er belegt, dass der Druckdienst wirklich
   antwortet. ICMP-Ping läuft ergänzend; schlägt er fehl, während TCP steht, gilt das
   Gerät als erreichbar, weil manche Drucker ICMP unterdrücken.
3. **Bei Fehlschlag Subnetzscan.** Wiedererkennung vorrangig über die **MAC-Adresse** aus
   der ARP-Tabelle, weil das beweiskräftig ist; hilfsweise über einen offenen Port 9100.
4. **Ursache benennen, nicht nur Symptom.** Wurde das Gerät unter einer anderen Adresse
   gefunden und stammt diese aus dem DHCP-Bereich, lautet die Meldung sinngemäß:

   > Der Bondrucker „Kasse 1" antwortet nicht unter 192.168.1.50. Er wurde unter
   > **192.168.1.87** gefunden. Diese Adresse hat er vom Router bekommen — das heißt, im
   > Drucker ist keine feste Adresse eingestellt. Deshalb geht sie nach jedem Stromausfall
   > verloren.

5. **Drei Handlungswege**, klar nach Wirkung sortiert:
   * **Sofort wieder drucken** — POS-Einstellung auf die gefundene Adresse ändern
     (über die POS-API, mit Anmeldedialog). Wirkt sofort, behebt die Ursache nicht.
   * **Dauerhaft beheben** — Drucker-Weboberfläche öffnen (`http://<ist-ip>`) plus
     modellgenaue Klickanleitung aus `vendors.py`, hergeleitet über die MAC-OUI.
   * **Testdruck** über die POS-API zur Bestätigung.

6. **Automatisches Setzen der Drucker-IP** ist Phase 2 (Abschnitt 15). `vendors.py` ist in
   Phase 1 eine reine Datentabelle: OUI-Präfix, Herstellername, Pfad der Weboberfläche,
   Anleitungstext-Schlüssel. Keine Plugin-Struktur ohne Implementierungen.

Erkennt die Netzwerkprüfung, dass Gateway oder Verbindung ausgefallen sind, meldet der
Drucker-Tab das als Ursache, statt den Drucker fälschlich als defekt darzustellen.

---

## 10. Reparaturaktionen

Alle mit Bestätigungsdialog in Kundensprache, alle mit Audit-Eintrag, alle mit
anschließender automatischer Neuprüfung des betroffenen Bereichs.

| Aktion | Rechte | Risiko | Wirkung |
|---|---|---|---|
| Testdruck | POS-Login | niedrig | Bon wird gedruckt |
| POS-Druckereinstellung ändern | POS-Login | niedrig | Setzt Drucker-IP im POS |
| Drucker-Weboberfläche öffnen | keine | keine | Öffnet Browser-Tab |
| Einzelnen `pos-*`-Container neu starten | sudo | mittel | Dienst kurz unterbrochen |
| Netzwerkdienst neu starten | sudo | mittel | Netz einige Sekunden weg |
| DHCP-Lease erneuern | sudo | niedrig | Neue Adresszuweisung |
| Zeitsynchronisation erzwingen | sudo | niedrig | Uhr wird gestellt |
| DNS-Zwischenspeicher leeren | sudo | niedrig | — |
| Verwaiste Docker-Images entfernen | sudo | niedrig | Nur unreferenzierte Images |
| `kassio-power-agent` neu starten | sudo | niedrig | Ausschaltknopf wieder verfügbar |
| Gerät neu starten / herunterfahren | sudo | hoch | Deutliche Warnung, doppelte Bestätigung |

Bewusst **nicht** enthalten: Neustart des gesamten Stacks, `compose down`, Image-Pull,
Update-Auslösung, Datenbankeingriffe, Löschen von Sicherungen, Entfernen getaggter Images.

Beim Entfernen verwaister Images werden ausschließlich unreferenzierte Images ohne Tag
angefasst. Die für einen Rücksprung des Updaters benötigten Images sind getaggt und
bleiben unberührt; der Bestätigungsdialog nennt vorher Anzahl und Größe.

---

## 11. Support-Report

Ein Knopf erzeugt eine einzelne `.txt`-Datei — bewusst kein Archiv, damit der Kunde sie
ohne Umweg einfügen oder anhängen kann. Erst oberhalb von etwa 2 MB wird auf ZIP
umgeschaltet.

Inhalt: Zeitstempel, Werkzeugversion, alle Prüfergebnisse mit Ist- und Sollwerten,
`expected-config` (enthält keine Geheimnisse), Container-Status und -Logs, Updater- und
Backup-Zustand, technische Details aller fehlgeschlagenen Prüfungen samt Tracebacks.

**Schwärzung nach Whitelist-Prinzip.** Es wird nicht versucht, Geheimnisse zu erkennen und
zu entfernen — es wird festgelegt, was überhaupt hineindarf. Die `.env` wird nie
eingebettet; aufgenommen wird nur die Liste ihrer **Schlüsselnamen** mit dem Vermerk
„gesetzt" oder „nicht gesetzt". Zusätzlich läuft über alle Logtexte ein Nachfilter für
`PASSWORD`, `TOKEN`, `SECRET`, `OTPK`, `Authorization`, `ghp_`, `Bearer`, JWT-Muster.
Beides ist testpflichtig.

---

## 12. Oberfläche

* Große Bedienziele — die Geräte haben Touchscreens.
* Status immer dreifach kodiert: Farbe **und** Symbol **und** Text; Farbe allein ist bei
  Farbfehlsichtigkeit wertlos.
* Jede Fehlermeldung nennt Ursache, Bedeutung und nächsten Schritt. Technische Details
  stecken hinter „Details anzeigen".
* Sprachumschalter oben rechts (de/en/ru), Voreinstellung aus `site.language`, sonst
  Deutsch, Auswahl im `localStorage` gemerkt.
* Helles und dunkles Erscheinungsbild über `prefers-color-scheme`.
* Kein CDN, keine externen Schriften, kein Build. Funktioniert vollständig offline.

---

## 13. HTTP-Schnittstelle

| Methode | Pfad | Rechte | Zweck |
|---|---|---|---|
| GET | `/` | — | Oberfläche |
| GET | `/api/health` | — | Lebenszeichen und Version |
| GET | `/api/i18n/{lang}` | — | Sprachdatei |
| GET | `/api/checks` | — | Alle Prüfungen (parallel, 5 s zwischengespeichert) |
| GET | `/api/checks/{group}` | — | Bereich neu ausführen |
| GET | `/api/containers` | — | Containerliste |
| GET | `/api/containers/{name}/logs?lines=` | — | Logs |
| POST | `/api/scan` | — | Subnetzscan |
| GET | `/api/config` | — | Sollzustand |
| PUT | `/api/config` | sudo | Sollzustand schreiben |
| POST | `/api/session` | — | Sudo-Passwort prüfen, Token ausgeben |
| DELETE | `/api/session` | Token | Sperren |
| POST | `/api/pos/session` | — | POS-Anmeldung |
| DELETE | `/api/pos/session` | Token | POS-Abmeldung |
| POST | `/api/actions/{id}` | je Aktion | Reparatur ausführen |
| GET | `/api/report` | — | Support-Report |

**Jede** Anfrage — auch die in der Tabelle mit „—" markierten — durchläuft zuvor die
Peer-UID-Prüfung aus Abschnitt 5.8; „—" bedeutet „ohne zusätzliche Anmeldung", nicht
„ohne Zugriffskontrolle". `POST /api/scan` ist zusätzlich ratenbegrenzt (Abschnitt 5.7).

Alle POST/PUT/DELETE verlangen den Header `X-Kassio-Diag: 1` und werden gegen die
Origin-Allowlist geprüft. Antworten sind stets JSON mit `{ok, data|error}`; ein Fehler
enthält immer einen Sprachschlüssel, nie einen nackten Text.

---

## 14. Installation

`sudo ./install.sh` ist der einzige Installationsmechanismus — auch für Bestandsgeräte
einzeln aufrufbar. `installer.py` erhält einen zusätzlichen Schritt, der genau dieses
Skript aufruft. Es gibt bewusst keinen zweiten Pfad, der auseinanderlaufen könnte.

Ablauf mit Vorprüfungen und Abbruch statt Verdrängung:

1. Root-Rechte, `python3` und `systemd` prüfen
2. Port 9120 auf Belegung prüfen — belegt heißt Abbruch mit Meldung
3. Administrationsbenutzer aus `SUDO_USER` bestimmen und dessen sudo-Berechtigung prüfen
4. Dateien nach `/opt/kassio-diagnostics` installieren, Eigentümer und Rechte setzen
5. sudoers-Drop-in temporär schreiben, mit `visudo -c -f` prüfen, erst dann aktivieren
6. Unit installieren, `daemon-reload`, aktivieren, starten
7. Gesundheitsprüfung gegen `/api/health` (Muster aus `kiosk-agent/install.sh`)
8. Desktop-Eintrag anlegen, Browser-Lesezeichen nach bestem Bemühen (nie fatal)
9. Zusammenfassung ausgeben: Adresse, Dienstname, Log-Kommando

Jeder Schritt ist wiederholbar (idempotent). `uninstall.sh` entfernt Dienst, sudoers-Regel,
Programmdateien, Desktop-Eintrag und Lesezeichen und fragt getrennt, ob
`/etc/kassio-diagnostics` erhalten bleiben soll.

---

## 15. Teststrategie

`pytest`, aufgerufen aus dem Repositoriumswurzelverzeichnis wie die vorhandenen
`test_*.py`. Die Testdateien selbst liegen in `diagnostics/tests/`. Tests laufen **ohne Netz
und ohne sudo**; die Kommandoausführung wird injiziert.

Verbindlich abgedeckt:

* **Argumentvalidierung des Helpers** — jedes Verb, jedes Zurückweisungsmuster, inklusive
  Versuchen mit Sonderzeichen, Pfadangaben und überlangen Namen. Verbindlich mit eigenen
  Fällen abgedeckt: **abschließender Zeilenumbruch** (`pos-backend\n` muss abgelehnt
  werden), **führender Bindestrich** und die Zusicherung, dass jedes zusammengebaute
  Kommando ein `--` vor den Werten enthält
* **Peer-UID-Prüfung** — Anfrage einer fremden UID wird mit `403` abgewiesen; ist die UID
  nicht bestimmbar, wird ebenfalls abgewiesen
* **Ratenbegrenzung des Scans** — zwei schnell aufeinanderfolgende Scans, der zweite wird
  abgelehnt
* **Schwärzung** — bekannte Geheimnisse müssen beweisbar aus dem Report verschwinden;
  dieser Test ist die Schutzlinie für ein öffentliches Repositorium
* **Isolation der Prüfungen** — eine absichtlich abstürzende Prüfung darf das
  Gesamtergebnis nicht verhindern
* **Degradationsmatrix aus 3.2** — je Zeile ein Test mit simuliertem Ausfall
* **Schema und Plausibilitätsprüfungen** der `expected-config`
* **Sprachdateien** — identische Schlüsselmenge **und** identische Platzhalter in de/en/ru;
  die vorhandenen Installer-Sprachdateien erfüllen das heute mit je 115 Schlüsseln
* **Fallback-Kette der Lokalisierung** bis hinunter zum Schlüsselnamen
* **Sitzungslogik** — Ablauf, Leerlauf-Timeout, Sperre nach Fehlversuchen
* **Parsen** von IP, MAC, OUI, Containerstatus, Updater-Zustand
* **Rückwirkungsfreiheit** — ein Test, der die Menge der geschriebenen Pfade gegen die
  Liste in Abschnitt 6 prüft

---

## 16. Umfang und Phasen

**Phase 1 (dieses Spec):** Dienst, Absicherung, sechs Tabs, alle Prüfungen aus Abschnitt 8,
alle Aktionen aus Abschnitt 10, Techniker-Assistent, Report, drei Sprachen, Installation
inklusive Einbindung in `installer.py`, vollständige Tests.

**Phase 2 (später, mit Testgerät):** Automatisches Setzen der Drucker-IP für Epson
(EpsonNet, UDP 3289) und Star (Telnet). Bewusst zurückgestellt: ungetesteter
Protokollcode in einem öffentlichen Repositorium kann einen Drucker unerreichbar machen —
genau den Zustand, den das Werkzeug beheben soll.

**Verworfen:** Automatischer Versand an den Support (Datenschutz, Internetabhängigkeit),
QR-Code-Übergabe (kein Server), LAN-Erreichbarkeit (Angriffsfläche), hinterlegte
POS-Zugangsdaten (Klartextgeheimnis auf dem Kundengerät), Stack-weiter Neustart
(Ausfallzeit ohne Not), Aufräumen von Container-Logs (Rotation bereits konfiguriert),
Endpunkt-Erkennung über `/openapi.json` (zusätzliche Fehlerquelle ohne Mehrwert gegenüber
einer guten Fehlermeldung).
