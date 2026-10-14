# Class notes pipeline

[![tests](https://github.com/lobby9000/class-notes-pipeline/actions/workflows/tests.yml/badge.svg)](https://github.com/lobby9000/class-notes-pipeline/actions/workflows/tests.yml)

Turns four-hour class recordings into structured Obsidian notes with no manual steps. Speech is transcribed locally on the CPU with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), and Claude Code in headless mode writes each note, a page per new concept and a course index for Obsidian's graph view. An optional extension answers questions about past classes from the notes and transcripts, citing the class and the minute, and can write the notes through the Anthropic API instead.

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
- **88 automated tests, none of them online.** The pipeline's 51 run against a fake Whisper model and a fake `claude` executable. The fake model checks every option it receives against the real `WhisperModel.transcribe` signature, and several tests decode real MP4 files generated with ffmpeg. The extension's 37 use a fake Anthropic API, the real Anthropic SDK over a fake HTTP transport, fake embeddings and a real Chroma database in a temporary folder.

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

## Asking questions of past classes

The extension in [`extensiones/`](extensiones/) is documented in Spanish, with its configuration and design decisions, in [EXTENSIONES.md](EXTENSIONES.md).

```text
$ pizza pregunta "¿cómo se calcula el ISR de un salario?"
El ISR de un salario se calcula con la tabla del artículo 96, restando el límite inferior [1].

Fuentes:
  [1] Clase 24 septiembre · minuto 00:47:12 (transcripción)
```

- **Chunks that keep their origin.** Each note is split into pieces of up to 1,200 characters: note sections with their heading path, `[!claude]` callouts kept apart so an answer that leans on Claude's additions can be flagged, and transcript stretches that keep the minute where they start.
- **Hybrid retrieval.** Multilingual embeddings (jina-embeddings-v2-base-es, ONNX on the CPU) match by meaning, BM25 over Spanish word stems catches exact terms such as acronyms, and Reciprocal Rank Fusion merges both rankings. The index lives in Chroma outside the vault and only reprocesses notes whose SHA-1 changed.
- **An agent with a search tool.** The first turn already carries the 8 best chunks. When they are not enough, Claude can call `buscar_apuntes` with other words, up to two more times.
- **No invented answers.** Claude only sees chunks of the notes and must cite each sentence. It answers `NO_ENCONTRADO` when the notes do not hold the answer, and the program then shows the closest chunks instead. An answer that cites no real chunk is never shown, citations to chunks that do not exist are removed with a warning, and the reader is warned when most of an answer is uncited.
- **API mode with a bill.** With `backend = "api"` the notes are written through the Anthropic API with one model per task (Opus for classes, Sonnet for meetings, Haiku for questions), streamed, and every call is logged with its tokens, cost, latency and time to first token.

| Command | Alias | What it does |
|---|---|---|
| `pizza pregunta "..."` | `apuntes preguntar` | Answers from the notes and transcripts, citing the class and the minute |
| `pizza despensa` | `apuntes indexar` | Updates the index with new or changed notes |
| `pizza cuenta` | `apuntes costos` | Tokens, cost and latency of every API call, by model and by task |
| `pizza cotiza "24 septiembre"` | `apuntes estimar` | What that note would cost with Opus, Sonnet or Haiku, with input tokens counted by the API |

The pipeline itself does not need any of this. The extension's packages, listed in `requirements-extensiones.txt`, are only imported when its commands run.

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

For the extension, `.venv/bin/python aplicar_extensiones.py` installs its packages and runs the whole suite. The installer looks for Python 3.11 or newer even when a conda environment with an older Python is active. `apuntes-vigilar.service` optionally runs `pizza turno` in the background as a systemd user service.

## Tests

```bash
.venv/bin/python -m unittest discover -s pruebas -v
```

CI runs the same suite on every push, with ffmpeg and the extension's packages installed.

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
| `extensiones/` | Questions with citations, API mode and the cost log |
| `EXTENSIONES.md` | How the extension works and how to configure it (Spanish) |
| `requirements-extensiones.txt` | The extension's packages: anthropic, chromadb, fastembed and snowballstemmer |
| `aplicar_extensiones.py` | Installs those packages and runs the tests, or adds the extension's hooks to an older `apuntes.py` |
| `pruebas/` | The test suite |

## Context

A personal tool for the Diploma in Finance at UNAM, running on a Ryzen 7 3700U laptop with 8 GB of RAM. The classes are in Spanish, so the code, CLI messages and prompts are too. Designed and built with Claude Code as a coding agent.

## Licence

Released under the [MIT licence](LICENSE).

---

Part of the portfolio of Luis Ángel Quiroz López · [lobby9000.github.io](https://lobby9000.github.io) · [LinkedIn](https://www.linkedin.com/in/llqq) · [GitHub](https://github.com/lobby9000)
