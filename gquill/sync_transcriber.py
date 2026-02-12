"""SyncTranscriber: livekeet Transcriber that syncs lines to Google Docs."""

from __future__ import annotations

from datetime import datetime

from livekeet import Transcriber

from gquill.doc_sync import DocSync


class SyncTranscriber(Transcriber):
    """Transcriber subclass that sends each transcript line to a Google Doc.

    Local file writes (via super) remain the source of truth.
    Google Doc sync is best-effort — failures are logged, never propagated.
    """

    def __init__(self, doc_sync: DocSync | None = None, **kwargs):
        super().__init__(**kwargs)
        self.doc_sync = doc_sync

    def _write_transcript(self, text: str, speaker: str | None = None):
        # Local file write first — always the source of truth
        super()._write_transcript(text, speaker)

        if self.doc_sync is None:
            return

        # Build the same markdown line that was written to the file
        timestamp = datetime.now().strftime("%H:%M:%S")
        if speaker:
            line = f"[{timestamp}] **{speaker}**: {text}"
        else:
            line = f"[{timestamp}] {text}"

        try:
            self.doc_sync.append_line(line)
        except Exception:
            pass  # best-effort; DocSync logs its own errors

    def stop(self):
        # Sync a footer line before shutting down
        if self.doc_sync is not None:
            footer = f"\n---\n*Ended: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"
            try:
                self.doc_sync.append_line(footer)
                self.doc_sync.shutdown()
            except Exception:
                pass

        super().stop()
