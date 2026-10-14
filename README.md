# Class notes pipeline

[![tests](https://github.com/lobby9000/class-notes-pipeline/actions/workflows/tests.yml/badge.svg)](https://github.com/lobby9000/class-notes-pipeline/actions/workflows/tests.yml)

Turns four-hour class recordings into structured Obsidian notes with no manual steps. Speech is transcribed locally on the CPU with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), and Claude Code in headless mode writes each note, a page per new concept and a course index for Obsidian's graph view.

```
lecture .mp4  →  Whisper on the CPU  →  transcript with part and time marks  →  claude -p  →  note + concept pages + index
```

[Versión en español](README.es.md)

## Why it exists

Classes for a finance diploma run up to four hours, are recorded on a tablet and often arrive in two or three parts because the recording stops during breaks. Transcribing them by hand in a web tool and pasting the text into a chatbot took longer than the class. This pipeline does the whole job unattended on an 8 GB laptop with no GPU, and only the text ever leaves the machine.

## Highlights

- **Fits in 8 GB of RAM.** faster-whisper builds the spectrogram of the whole input in memory, so a two-hour recording needed more than 6 GB and was killed by the kernel. The pipeline now transcribes in ten-minute chunks cut at silences, which brought the audio's peak memory down to about 0.7 GB on the same recording.
- **One class, one note.** All recordings from the same day become a single session ("Clase 24 septiembre"), and recordings that start before 5 a.m. count for the previous day. When a missing part arrives later, only that part is transcribed and the note is rebuilt with every part.
- **Model additions stay visible.** The prompt asks for zero information loss and keeps whatever Claude adds or corrects apart from what was said in class: italics for short additions and `> [!claude]` callouts for longer ones, coloured by a CSS snippet in Obsidian. Mistakes made in class that were not corrected there are flagged rather than silently fixed.
- **Safe to run repeatedly.** Transcriptions are cached per video, progress lives in a `registro.json`, files are written atomically, a lock stops two runs from overlapping and the laptop is kept awake while it works. Regenerating a note moves the old one to a backup folder, and existing concept pages are never overwritten.
- **51 automated tests** run against a fake Whisper model and a fake `claude` executable, so nothing is downloaded. The fake model checks every option it receives against the real `WhisperModel.transcribe` signature, and several tests decode real MP4 files generated with ffmpeg.

## Commands

The commands have pizzeria names. The original names (`apuntes`, `apuntes estado` and so on) still work as aliases.

| Command | Alias | What it does |
|---|---|---|
| `pizza` | `apuntes` | Processes every new class recording in the videos folder |
| `pizza menu` | `apuntes estado` | Lists which sessions are ready and which are pending |
| `pizza pedido clase.mp4 --materia "estadística"` | `apuntes suelto` | A one-off class or meeting (`--tipo reunion --titulo "..."`) |
| `pizza recalienta "24 septiembre"` | `apuntes rehacer` | Regenerates a note and keeps the previous one. `--transcribir` also redoes the transcription |
| `pizza cata` | `apuntes prueba` | Measures how long each Whisper model takes on this laptop |
| `lets make pizza` or `pizza turno` | `apuntes vigilar` | Opens LocalSend to receive videos from the tablet over Wi-Fi and processes each class as it arrives |

## What appears in the vault

| Where | What |
|---|---|
| `Diplomado Finanzas/Clase 24 septiembre.md` | The class note, with the full transcript folded at the end |
| `Diplomado Finanzas/Índice del diplomado.md` | Every class with its topics and concepts, regenerated automatically |
| `Conceptos/Saldo insoluto.md` | One page per new concept, ready for your own description |
| `Notas sueltas/` | One-off classes and meetings |

[`ejemplos/vault-ejemplo/`](ejemplos/vault-ejemplo/) is a sample vault with a real note, its concept pages and the index. Open it in Obsidian with *Open folder as vault*.

## Install

Requires Linux, Python 3.11 or newer, ffmpeg and [Claude Code](https://docs.claude.com/en/docs/claude-code/overview).

```bash
git clone https://github.com/lobby9000/class-notes-pipeline.git ~/apuntes-clase
cd ~/apuntes-clase
bash instalar.sh          # creates .venv, installs faster-whisper and the pizza/apuntes commands
nano config.toml          # set the path to your Obsidian vault
pizza cata                # optional: time each Whisper model on your machine
```

The installer looks for Python 3.11 or newer even when a conda environment with an older Python is active. `apuntes-vigilar.service` optionally runs `pizza turno` in the background as a systemd user service.

## Tests

```bash
.venv/bin/python -m unittest discover -s pruebas -v
```

CI runs the same suite on every push with ffmpeg installed.

## Lessons from real recordings

- PyAV 19.0.0 removed a parameter that faster-whisper uses to decode audio, which broke every transcription. The dependency is pinned to `av<19` until faster-whisper adapts.
- Whisper sometimes invents YouTube subtitle credits ("subtítulos realizados por la comunidad de Amara.org") during silences. Segments made only of those phrases are dropped.
- Deleting a video after it had been transcribed could rebuild its class note without that part. Cached transcriptions now count even when the source video is gone.

## Files

| File | Purpose |
|---|---|
| `apuntes.py` | The whole program, standard library plus faster-whisper |
| `config.ejemplo.toml` | Configuration template (folders, note titles, Whisper model, domain vocabulary) |
| `prompts/clase.md`, `prompts/reunion.md` | The instructions Claude receives. Edit these to change the notes |
| `obsidian/claude-callout.css` | Colours for what Claude added |
| `CLAUDE.md`, `.claude/skills/ajustar-apuntes/` | Context and a skill for changing the note format with Claude Code |
| `pruebas/` | The test suite |

## Context

A personal tool for the Diploma in Finance at UNAM, running on a Ryzen 7 3700U laptop with 8 GB of RAM. The classes are in Spanish, so the code, CLI messages and prompts are too. Designed and built with Claude Code as a coding agent.

## Licence

Released under the [MIT licence](LICENSE).

---

Part of the portfolio of Luis Ángel Quiroz López · [lobby9000.github.io](https://lobby9000.github.io) · [LinkedIn](https://www.linkedin.com/in/llqq) · [GitHub](https://github.com/lobby9000)
