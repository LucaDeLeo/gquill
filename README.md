# gquill

Real-time meeting transcription synced to Google Docs. Powered by [livekeet](https://github.com/LucaDeLeo/livekeet) (on-device transcription via NVIDIA Parakeet on Apple Silicon) and [gdoc](https://github.com/LucaDeLeo/gdoc) (CLI for Google Docs).

Each line of transcript appears in a Google Doc within seconds of being spoken. The local markdown file is always the source of truth — Google sync is best-effort and never blocks transcription.

## Install

```
uv tool install git+https://github.com/LucaDeLeo/gquill.git
```

## Setup

### Google Auth (one-time)

```
gquill auth
```

This opens a browser for Google OAuth and saves a token to `~/.config/gdoc/token.json`. All docs are created under whichever Google account you sign in with. To switch accounts, run `gquill auth` again.

### Parakeet Model

Downloads automatically on first run (~600MB, cached for subsequent runs).

## Usage

```
gquill auth                     # authenticate with Google (one-time)
gquill                          # transcribe + sync to a new Google Doc
gquill --with "Alice"           # label the other speaker
gquill --doc DOC_ID_OR_URL      # append to an existing doc
gquill --mic-only               # microphone only (no system audio)
gquill --no-sync                # local-only, no Google Doc
gquill --multilingual           # use multilingual model (v3)
gquill -o meeting.md            # custom local output file
```

Press `Ctrl+C` to stop. The local transcript and Google Doc both get a timestamped footer.

## How it works

1. Parse args, load livekeet config
2. Validate Google auth (exits with error if not authenticated — run `gquill auth` or use `--no-sync` for local-only)
3. Create a new Google Doc (or open existing via `--doc`) and print the URL
4. Start a background sync thread that consumes a queue of transcript lines
5. Start transcribing — audio is captured, run through VAD, and transcribed on-device
6. Each transcript line is written to the local file first, then enqueued for Google Doc sync
7. `Ctrl+C` syncs a footer, drains the queue, and exits

## Architecture

gquill is a thin integration layer — no code is duplicated from either dependency.

```
gquill/
├── cli.py                # CLI args + orchestration
├── doc_sync.py           # DocSync: queue + background thread → Google Docs API
└── sync_transcriber.py   # SyncTranscriber: subclasses livekeet.Transcriber
```

- **SyncTranscriber** subclasses `livekeet.Transcriber` and overrides `_write_transcript()` to enqueue each line for sync after writing it locally.
- **DocSync** runs a daemon thread that parses each markdown line into Google Docs API requests and sends them via `batchUpdate`. It tracks the document's end index locally to avoid extra API calls, and resyncs from the document after 3 consecutive failures.

## Requirements

- macOS with Apple Silicon (for livekeet's MLX-based transcription)
- Python 3.12+
- Google account with Docs API access
