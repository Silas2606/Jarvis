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

| Engine   | Qualität | Installation |
|----------|----------|--------------|
| `piper`  | sehr gut, lokal, schnell | [Binary + Stimmmodell](https://github.com/rhasspy/piper) nach `~/.local/share/piper-voices/` |
| `edge`   | exzellent, aber online | `pip install -e ".[edge]"` plus `ffmpeg` |
| `say`    | gut (macOS eingebaut) | — |
| `espeak` | robotisch, aber überall da | `sudo apt install espeak-ng` |

Für den Jarvis-Charakter empfohlen: Piper mit `de_DE-thorsten-medium` (deutsch)
oder `en_GB-alan-medium` (englisch).

> **Kopfhörer empfohlen.** Ohne sie hört Jarvis über die Lautsprecher seine
> eigene Stimme. Er kommt damit zurecht — Barge-in verlangt eine Drittelsekunde
> durchgehende Sprache, bevor er sich unterbrechen lässt — aber mit Kopfhörern
> reagiert er zuverlässiger.

### 3. Kalender und Mail

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

84 Tests, ohne API-Schlüssel und ohne Mikrofon lauffähig: ein skriptbarer
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
  voice/             Mikrofon, VAD, Whisper, Wake-Word, TTS, der Loop
  ui/console.py      das HUD
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
| Unterbricht sich selbst | Kopfhörer, oder `barge_in = false` |
| Bricht mitten im Wort ab | Ebenfalls Echo — siehe oben |

Wenn etwas mit der Claude-API nicht stimmt, sagt Jarvis es in einem Satz —
kein Guthaben, abgelehnter Schlüssel, Anfragelimit, Überlastung. Die
technische Meldung der API steht darunter in der Konsole, wird aber nicht
vorgelesen.

---

## Lizenz

MIT.
