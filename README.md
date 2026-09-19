# J.A.R.V.I.S.

> *Just A Rather Very Intelligent System*

Ein sprachgesteuerter persönlicher Assistent nach dem Vorbild aus Iron Man. Er
hört auf sein Wake-Word, versteht gesprochene Anweisungen, führt sie mit
Werkzeugen aus und antwortet laut — in ganzen Sätzen, während er noch denkt.

Als Gehirn dient **Claude Opus 5** über die Messages API. Die Ohren (Whisper)
und die Stimme (Piper) laufen lokal auf deinem Rechner.

```
  ╔════════════════════════════════════════════╗
  ║      J · A · R · V · I · S                 ║
  ║      Just A Rather Very Intelligent System ║
  ╚════════════════════════════════════════════╝
  Good to see you, Sir.
  brain claude-opus-5   ears faster-whisper   voice piper   tools 28

  17:04  ◉ listening…

  Sir: Jarvis, wie sieht mein Tag aus?
  ⚙ calendar_list(start=today, end=+1d)
  JARVIS: Drei Termine, Sir. Der erste ist um neun mit Frau Meier,
          danach haben Sie bis vierzehn Uhr frei.
```

---

## Die Oberfläche

`jarvis` öffnet ein eigenes Fenster: der Kern in der Mitte reagiert auf das,
was gerade passiert — ruhig im Wartezustand, grün und im Takt deiner Stimme
beim Zuhören, bernsteinfarben beim Arbeiten. Links Systemzustand, Mikrofonpegel
und ein Protokoll der Werkzeugaufrufe, rechts Aufgaben, Erinnerungen und
Termine, unten das Gespräch und ein Eingabefeld für den Fall, dass Tippen
gerade passender ist als Sprechen.

**Jede Anzeige trägt echte Daten.** Es gibt hier keine dekorativen Balken.

```bash
jarvis                 # Fenster öffnen (Standard)
jarvis --no-window     # dieselbe Oberfläche im Browser
jarvis --console       # nur Terminal, wie bisher
jarvis --text          # Tastatur statt Mikrofon
```

Für ein echtes Fenster statt eines Browser-Tabs:

```bash
pip install -e ".[window]"
```

Ohne dieses Paket öffnet sich die Oberfläche im Standardbrowser — sie
funktioniert identisch. Der Server lauscht nur auf `127.0.0.1` und verlangt
ein bei jedem Start neu erzeugtes Token; ohne das kommt keine andere Seite an
dein Gespräch.

## Schnellstart

```bash
git clone <dieses-repo> && cd Jarvis
pip install -e ".[voice,google]"
export ANTHROPIC_API_KEY=sk-ant-…

jarvis doctor        # prüft, was noch fehlt
jarvis               # los geht's — sag "Jarvis"
```

Kein Mikrofon zur Hand? `jarvis --text` gibt dir denselben Assistenten über die
Tastatur.

---

## Installation

### 1. Das Gehirn

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-…
```

Alternativ `ant auth login` — das SDK findet das Profil von selbst.

### 2. Ohren und Stimme

```bash
pip install -e ".[voice]"
```

Das bringt `faster-whisper` (Spracherkennung), `openwakeword` (Wake-Word),
`webrtcvad` und `sounddevice` mit. Unter Linux braucht PortAudio noch ein
Systempaket:

```bash
sudo apt install portaudio19-dev    # Debian/Ubuntu
brew install portaudio              # macOS
```

**Die Stimme** wählt Jarvis automatisch. In dieser Reihenfolge:

| Engine | Qualität | Installation |
|---|---|---|
| `elevenlabs` | kaum von einem Menschen zu unterscheiden, kostenpflichtig | nur `ELEVENLABS_API_KEY` setzen |
| `piper` | sehr gut, lokal, schnell | [Binary + Stimmmodell](https://github.com/rhasspy/piper) nach `~/.local/share/piper-voices/` |
| `edge` | gut, online, gratis | `pip install -e ".[edge]"` plus `ffmpeg` |
| `say` | brauchbar (macOS eingebaut) | — |
| `espeak` | robotisch, aber überall da | `sudo apt install espeak-ng` |

Sobald ein `ELEVENLABS_API_KEY` gesetzt ist, nimmt Jarvis ihn von selbst — das
ist ja der Grund, warum man einen setzt.

### ElevenLabs

Die natürlichste Stimme, die Jarvis sprechen kann. Schlüssel auf
[elevenlabs.io](https://elevenlabs.io) unter *Profile* erzeugen, dann:

```powershell
setx ELEVENLABS_API_KEY "..."      # Windows, danach neues Fenster
export ELEVENLABS_API_KEY="..."    # macOS/Linux
```

```bash
jarvis voices                       # zeigt die Stimmen deines Kontos
jarvis say "Guten Abend, Sir."
```

Eine Stimme festlegen — **Name oder ID, beides geht**, ein eindeutiger
Namensanfang reicht auch:

```toml
[voice]
tts_engine = "elevenlabs"
tts_voice = "Roger"                      # oder "CwhRBWXzGAHq8TQ4Fs17"
elevenlabs_model = "eleven_flash_v2_5"   # schnellstes; eleven_multilingual_v2 klingt besser
```

Zum Ausprobieren ohne die Datei anzufassen:

```powershell
$env:JARVIS_VOICE_TTS_VOICE = "Roger"
jarvis say "Guten Abend, Sir."
```

Jarvis fordert das Audio als rohes PCM an und spielt es ab, während es noch
entsteht — dadurch beginnt die Stimme früher als bei den anderen Engines, und
ffmpeg wird nicht gebraucht. Erlaubt der Tarif kein PCM, fällt er einmalig auf
MP3 zurück.

Den Schlüssel findest du in den Workspace-Einstellungen unter *API Keys*
([direkter Link](https://elevenlabs.io/app/settings/api-keys)) — er wird nur
einmal angezeigt.

**Wenn das Guthaben leer ist, verstummt Jarvis nicht.** Er wechselt auf die
beste freie Stimme, die installiert ist, sagt einmal Bescheid und macht weiter:

```
  elevenlabs is unavailable (quota exhausted); switching to edge until 01.10. 00:00.
```

Den Zeitpunkt errät er nicht — ElevenLabs meldet selbst, wann das Kontingent
zurückgesetzt wird. Dann probiert er den nächsten Satz wieder mit der guten
Stimme und bleibt dabei, wenn es klappt:

```
  elevenlabs is available again.
```

Der Zustand überlebt Neustarts, damit nicht jeder Start eine aussichtslose
Anfrage an ein leeres Konto verschwendet. `jarvis doctor` zeigt, ob gerade
pausiert wird und bis wann; wer sofort neu probieren will, löscht
`~/.jarvis/tts_state.json`. Abschalten mit `tts_fallback = false`.

Die Abrechnung läuft nach Zeichen. Im kostenlosen Tarif sind 10.000 Zeichen
pro Monat etwa sechzig bis hundertfünfzig gesprochene Antworten — zum
Ausprobieren gut, für den Alltag zu wenig. Genau dafür gibt es den Rückfall.

> **Kopfhörer empfohlen.** Ohne sie hört Jarvis über die Lautsprecher seine
> eigene Stimme. Er kommt damit zurecht — Barge-in verlangt eine Drittelsekunde
> durchgehende Sprache, bevor er sich unterbrechen lässt — aber mit Kopfhörern
> reagiert er zuverlässiger.

### 3. Microsoft To Do

```bash
pip install -e ".[microsoft]"
```

Im [Entra-Portal](https://entra.microsoft.com) unter *Identity → App
registrations → New registration* eine App anlegen (Name beliebig, Kontotyp:
persönliche Microsoft-Konten). In der App unter *Authentication* →
**Allow public client flows: Ja**. Dann die *Application (client) ID* eintragen:

```toml
[tools]
microsoft_client_id = "…"
tasks_backend = "microsoft"
```

Und einmalig anmelden:

```bash
jarvis setup microsoft
```

Er zeigt einen kurzen Code, den du auf
[microsoft.com/devicelogin](https://microsoft.com/devicelogin) eingibst — auch
vom Handy aus. Danach landet „setz das auf die Liste" in To Do statt in der
lokalen Liste; die Werkzeuge heißen gleich, Jarvis merkt den Unterschied
nicht. Angefragt wird nur `Tasks.ReadWrite` — Mail und Dateien bleiben außen
vor.

### 4. Seiten hinter Login lesen

```bash
pip install -e ".[browser]"
playwright install chromium
```

Jarvis bekommt ein eigenes Browser-Profil unter `~/.jarvis/browser-profile`.
Pro Seite meldest du dich dort einmal an:

```bash
jarvis browser-login studio.youtube.com
```

Es öffnet sich ein sichtbares Fenster — anmelden, Fenster schließen, fertig.
Die Sitzung bleibt in Jarvis' Profil, dein eigener Browser bleibt unberührt.
Danach kann er solche Seiten lesen und zusammenfassen.

> Das Profil enthält deine Anmelde-Cookies und ist damit so schützenswert wie
> ein Passwortmanager. Es liegt unter `~/.jarvis` und verlässt den Rechner
> nicht. Gelesen wird nur — geklickt, getippt oder abgeschickt wird nichts.

Jarvis nimmt dafür bevorzugt dein installiertes Chrome oder Edge statt des
mitgelieferten Chromium, weil manche Anmeldeseiten automatisierte Browser
abweisen („Dieser Browser ist möglicherweise nicht sicher"). **Bei Google
klappt die Anmeldung trotzdem oft nicht** — Google erkennt ferngesteuerte
Fenster recht zuverlässig und ändert die Erkennung laufend. Für YouTube
brauchst du das aber gar nicht: Die Zahlen kommen über die Analytics API.

**Für YouTube brauchst du das nicht.** Kanalzahlen kommen über die Analytics
API, und zwar als Daten statt als gerenderte Seite — genauer, schneller und
stabil gegenüber Umbauten der Oberfläche. Dafür nur:

```bash
jarvis setup google
```

Das ist auch dann nötig, wenn Google schon eingerichtet ist: Die
YouTube-Berechtigungen sind neu hinzugekommen und müssen einmal bestätigt
werden.

### 5. Kalender und Mail

```bash
pip install -e ".[google]"
```

Dann in der [Google Cloud Console](https://console.cloud.google.com/) ein
OAuth-Client vom Typ *Desktop* anlegen, Calendar- und Gmail-API aktivieren, die
JSON-Datei nach `~/.jarvis/google_client_secret.json` legen und einmalig:

```bash
jarvis setup google
```

---

## Reden mit ihm

Sag **„Jarvis"** (oder „Hey Jarvis") und dann, was du willst. Nach der Antwort
bleibt er zwölf Sekunden lang wach — Nachfragen brauchen kein Wake-Word mehr.

```
„Jarvis, erinnere mich in zwanzig Minuten daran, den Ofen auszuschalten."
„Was steht morgen an?"
„Setz mir einen Termin mit Anna am Donnerstag um zehn, eine Stunde."
„Hab ich neue Mails?"
„Lies mir die von Anna vor."
„Antworte ihr, dass ich den Termin bestätige."
„Merk dir, dass ich meinen Kaffee schwarz trinke."
„Was gibt's Neues über den Mars-Rover?"
„Schreib auf: Idee für das Reaktor-Gehäuse."
```

Du kannst ihm jederzeit ins Wort fallen — er hört sofort auf zu reden und hört
dir zu.

---

## Befehle

| Befehl | Wirkung |
|---|---|
| `jarvis` | Sprachmodus — der Normalfall |
| `jarvis --text` | Tastaturmodus, ohne Mikrofon |
| `jarvis ask "..."` | Eine einzelne Anweisung, dann Schluss |
| `jarvis doctor` | Zeigt, was installiert ist und was fehlt |
| `jarvis devices` | Listet die Audiogeräte |
| `jarvis say "..."` | Testet die Stimme |
| `jarvis listen` | Nimmt einmal auf und zeigt das Transkript |
| `jarvis voices` | Listet die Stimmen, die deine Engine akzeptiert |
| `jarvis setup google` | Autorisiert Kalender und Mail |
| `jarvis setup microsoft` | Autorisiert Microsoft To Do |
| `jarvis browser-login <url>` | Meldet Jarvis' Browser-Profil bei einer Seite an |
| `jarvis setup config` | Schreibt eine Beispiel-Konfiguration |

Nützliche Schalter: `--verbose` (zeigt Denkprozess und Tool-Ergebnisse),
`--language en`, `--model`, `--effort high`.

---

## Konfiguration

`~/.jarvis/config.toml`, angelegt mit `jarvis setup config`. Alles ist optional.

```toml
address  = "Sir"              # wie er dich anspricht
timezone = "Europe/Berlin"

[brain]
model  = "claude-opus-5"
effort = "low"                # low | medium | high | xhigh | max

[voice]
language        = "de"
wake_words      = ["jarvis", "hey jarvis"]
followup_window = 12.0        # Sekunden ohne Wake-Word nach einer Antwort
barge_in        = true        # ins Wort fallen erlaubt
tts_engine      = "auto"

[tools]
confirm_outbound = true       # fragt vor Mail-Versand und Terminlöschung
```

Jeder Wert lässt sich auch per Umgebungsvariable setzen:
`JARVIS_VOICE_LANGUAGE=en`, `JARVIS_ADDRESS=Silas`, `JARVIS_BRAIN_EFFORT=high`.

**Warum `effort = "low"`?** Sprache ist latenzkritisch. Das Denken bleibt aktiv
(adaptive thinking), aber Jarvis antwortet im Tempo eines Gesprächs statt eines
Aufsatzes. Für knifflige Fragen: `jarvis --effort high`.

---

## Was er kann

**Gedächtnis** — Notizen, Aufgaben mit Fälligkeit, Erinnerungen und dauerhafte
Fakten über dich. Alles in SQLite unter `~/.jarvis/jarvis.db`, überlebt
Neustarts. Was du ihm einmal sagst, weiß er beim nächsten Mal noch.

**Erinnerungen** — laufen in einem Hintergrund-Thread und melden sich laut, auch
mitten im Gespräch.

**Kalender** — Termine ansehen, anlegen, verschieben, löschen, und freie Slots
für eine Besprechung finden.

**Mail** — Ungelesenes, Suche in Gmail-Syntax, Vorlesen, Entwürfe, Senden und
Antworten im Thread.

**Recherche** — Websuche und Seitenabruf laufen über Anthropics serverseitige
Tools. Kein zweiter API-Schlüssel nötig.

**Browser** — „Mach YouTube auf", „such mir das", oder eine Seite abrufen und
zusammenfassen lassen. Abgerufen werden nur öffentliche Adressen: Anfragen an
`localhost`, private Netze und Cloud-Metadaten werden abgelehnt, und zwar
anhand der aufgelösten IP, nicht des Namens. Das ist kein Schutz vor dir,
sondern vor Texten, die Jarvis liest — eine Mail kann ihn sonst auffordern,
Dienste in deinem Netz abzufragen.

**YouTube** — Kanalzahlen aus der Analytics API: Aufrufe, Wiedergabezeit,
Abonnenten, die besten Videos eines Zeitraums, Traffic-Quellen, Tagesverlauf.
Läuft über deinen bereits autorisierten Google-Zugang, nur lesend.

**Seiten hinter Login** — für alles, was erst durch JavaScript entsteht oder
eine Anmeldung braucht, lädt Jarvis die Seite in einem echten Browser. Dafür
hat er ein eigenes Profil, getrennt von deinem.

**Zeit** — die Rechnung, bei der Sprachmodelle am häufigsten danebenliegen,
macht ein getesteter Parser: „in zehn Minuten", „übermorgen halb drei",
„Donnerstag", ISO-Zeitstempel.

### Bevor etwas nach draußen geht

Mail senden und Termine löschen fragen nach — laut, und du antwortest laut:

```
  ❓ Shall I send the mail to anna@example.com with the subject Terminbestätigung?
  Sir: Ja, schick sie ab.
```

„Ich weiß nicht" oder „Moment" gilt nicht als Zustimmung — er fragt nach.
Schweigen zählt als Nein. Abschalten lässt sich das mit
`confirm_outbound = false`.

---

## Wie es funktioniert

```
  Mikrofon ─► Wake-Word ─► Aufnahme (VAD) ─► Whisper ─► Text
                                                          │
                                                          ▼
                                        ┌─────────────────────────────┐
                                        │  Claude Opus 5              │
                                        │  Streaming-Tool-Loop        │
                                        └──────────┬──────────────────┘
                                          Tokens   │   Tool-Aufrufe
                                                   ▼
                              Satz-Zerlegung ─► Sprech-Queue ─► Stimme
                                                   ▲
                                          Barge-in ┘ (Mikrofon hört mit)
```

Ein paar Entscheidungen, die den Unterschied machen:

**Manueller Agent-Loop statt Tool Runner.** Der SDK-Runner nimmt einem die
Schleife ab, gibt aber den Token-Strom nicht heraus und lässt sich nicht mitten
im Satz abbrechen. Beides braucht ein Sprachassistent, also läuft die Schleife
in `jarvis/brain.py` selbst — inklusive `pause_turn`-Fortsetzung für die
Server-Tools.

**Sprechen, bevor der Satz zu Ende gedacht ist.** Die Token laufen durch einen
Satz-Zerleger (`speech_chunks.py`), der Abkürzungen und Uhrzeiten kennt — „z. B."
und „17.30 Uhr" beenden keinen Satz. Der *erste* Satz darf kürzer sein als die
folgenden: früh loszulegen ist das, was sich wie Reaktionsschnelligkeit anfühlt.

**Prompt-Caching mit stabilem Präfix.** Die Persona ist über eine Sitzung hinweg
bytegleich und trägt den Cache-Breakpoint; Uhrzeit und offene Aufgaben stehen
*dahinter*, wo sie den Cache nicht jede Minute entwerten. Die Tool-Reihenfolge
ist stabil sortiert — aus demselben Grund.

**Alles degradiert.** Kein `webrtcvad`? Energiebasierte Sprachaktivitätserkennung
mit adaptivem Rauschboden. Kein `openwakeword`? Dann wird transkribiert und im
Text nach dem Namen gesucht — wobei „Ich habe Jarvis gestern gesehen" ihn
absichtlich *nicht* weckt. Keine Stimme? Er antwortet schriftlich weiter.

---

## Entwicklung

```bash
pip install -e ".[dev]"
python -m pytest -q
```

189 Tests, ohne API-Schlüssel und ohne Mikrofon lauffähig: ein skriptbarer
Fake-Client (`tests/conftest.py`) spielt Claude, sodass Tool-Runden,
Abbrüche, Ablehnungen, Fehlerpfade und der Gesprächsverlauf echt geprüft
werden.

```
jarvis/
  brain.py           Agent-Loop gegen die Messages API
  apierrors.py       API-Fehler → ein Satz, den man vorlesen kann
  speech_chunks.py   Token-Strom → sprechbare Sätze
  timing.py          „in zehn Minuten" → Zeitstempel
  memory.py          SQLite: Notizen, Aufgaben, Erinnerungen, Fakten
  persona.py         der Systemprompt
  scheduler.py       Erinnerungs-Thread
  session.py         setzt alles zusammen
  tools/             die Werkzeuge, als Schemas aus Signatur + Docstring
  tools/browser_tools.py    Seiten öffnen und lesen
  tools/microsoft_tools.py  Microsoft To Do, unter denselben Namen
  tools/youtube_tools.py    Kanalzahlen aus der Analytics API
  tools/page_render.py      Seiten, die erst durch JavaScript entstehen
  voice/             Mikrofon, VAD, Whisper, Wake-Word, TTS, der Loop
  voice/elevenlabs.py  ElevenLabs, als Stream direkt in die Soundkarte
  voice/fallback.py    weicht auf eine freie Stimme aus, wenn das Guthaben leer ist
  ui/console.py      das HUD im Terminal
  ui/server.py       lokaler Server: Ereignisstrom für die Oberfläche
  ui/web/            die Oberfläche selbst (HTML, CSS, Canvas)
```

---

## Wenn etwas klemmt

**`jarvis doctor`** zuerst — es nennt für jedes Problem den Befehl, der es löst.

| Symptom | Ursache |
|---|---|
| Hört nicht zu | `jarvis devices`, dann `input_device` in der Config setzen |
| Wacht nicht auf | `jarvis listen` — kommt das Transkript an? Sonst Mikrofonpegel prüfen |
| Wacht ständig auf | Kopfhörer benutzen, oder `openwakeword` installieren |
| Stimme fehlt | `jarvis say "Test"` zeigt die genaue Ursache |
| Stimme abgelehnt | `jarvis voices` — der Name muss exakt stimmen |
| Stimme klingt steif | Eine andere aus `jarvis voices` probieren; die Multilingual-Stimmen klingen am natürlichsten |
| Plötzlich andere Stimme | ElevenLabs-Guthaben leer — `jarvis doctor` sagt, bis wann pausiert wird |
| „elevenlabs paused" trotz Guthaben | Falscher Schlüssel oder falsche Stimme. Nach 15 Minuten wird erneut probiert; sofort geht es mit `rm ~/.jarvis/tts_state.json`. `jarvis doctor` zeigt den echten Zeichenstand |
| Unterbricht sich selbst | Kopfhörer, oder `barge_in = false` |
| „insufficient permissions" bei YouTube | `jarvis setup google` erneut ausführen — die YouTube-Rechte sind neu |
| Antwortet träge | `jarvis doctor` — sagt *voice activity: energy fallback*? Dann `pip install webrtcvad-wheels` (Windows) |
| Bricht mitten im Wort ab | Ebenfalls Echo — siehe oben |

Wenn etwas mit der Claude-API nicht stimmt, sagt Jarvis es in einem Satz —
kein Guthaben, abgelehnter Schlüssel, Anfragelimit, Überlastung. Die
technische Meldung der API steht darunter in der Konsole, wird aber nicht
vorgelesen.

---

## Lizenz

MIT.
