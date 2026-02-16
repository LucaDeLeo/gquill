"""Background thread that syncs transcript lines to a Google Doc."""

from __future__ import annotations

import logging
import queue
import threading

from gdoc.api.docs import get_docs_service
from gdoc.mdparse import parse_markdown, to_docs_requests

log = logging.getLogger(__name__)

_SENTINEL = None  # signals the worker to shut down


def _inject_tab_id(requests: list[dict], tab_id: str) -> list[dict]:
    """Inject tabId into all Location and Range objects in Docs API requests."""
    for req in requests:
        for value in req.values():
            if isinstance(value, dict):
                if "location" in value:
                    value["location"]["tabId"] = tab_id
                if "range" in value:
                    value["range"]["tabId"] = tab_id
    return requests


class DocSync:
    """Queue-based background syncer: transcript lines → Google Docs API.

    Each line is parsed as markdown, converted to Docs API requests,
    and sent via batchUpdate. The end_index is tracked locally to avoid
    extra GET calls after each insert.
    """

    def __init__(self, doc_id: str, end_index: int, *, tab_id: str | None = None):
        self.doc_id = doc_id
        self.end_index = end_index
        self.tab_id = tab_id
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._consecutive_failures = 0
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def append_line(self, line: str) -> None:
        """Enqueue a markdown line for syncing (non-blocking)."""
        self._queue.put(line)

    def shutdown(self) -> None:
        """Signal the worker to drain remaining items and exit."""
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=15)

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                break
            try:
                self._sync_line(item)
                self._consecutive_failures = 0
            except Exception:
                self._consecutive_failures += 1
                log.exception("Failed to sync line to Google Doc")
                if self._consecutive_failures >= 3:
                    self._resync_index()

    def _sync_line(self, line: str) -> None:
        parsed = parse_markdown(line)
        if not parsed.plain_text:
            return

        # Ensure the line ends with a newline
        text = parsed.plain_text
        if not text.endswith("\n"):
            text += "\n"
            parsed = parse_markdown(line if line.endswith("\n") else line + "\n")

        requests = to_docs_requests(parsed, self.end_index)
        if not requests:
            return

        if self.tab_id:
            _inject_tab_id(requests, self.tab_id)

        service = get_docs_service()
        service.documents().batchUpdate(
            documentId=self.doc_id,
            body={"requests": requests},
        ).execute()

        self.end_index += len(text)

    def _resync_index(self) -> None:
        """Re-read the document to correct end_index after failures."""
        try:
            service = get_docs_service()
            if self.tab_id:
                from gdoc.api.docs import flatten_tabs

                doc = service.documents().get(
                    documentId=self.doc_id, includeTabsContent=True,
                ).execute()
                tabs = flatten_tabs(doc.get("tabs", []))
                tab = next(
                    (t for t in tabs if t["id"] == self.tab_id), None,
                )
                if tab:
                    content = tab["body"].get("content", [])
                else:
                    content = []
            else:
                doc = service.documents().get(
                    documentId=self.doc_id,
                ).execute()
                content = doc.get("body", {}).get("content", [])

            if content:
                self.end_index = content[-1].get("endIndex", 1)
            else:
                self.end_index = 1
            self._consecutive_failures = 0
            log.info("Resynced end_index to %d", self.end_index)
        except Exception:
            log.exception("Failed to resync end_index")
