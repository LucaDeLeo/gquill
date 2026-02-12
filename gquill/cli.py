"""CLI entry point for gquill."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from livekeet import (
    ensure_unique_path,
    load_config,
    resolve_device,
    resolve_output_path,
)

log = logging.getLogger(__name__)


def _setup_doc(args, config) -> tuple[str | None, str | None, int]:
    """Create or open a Google Doc. Returns (doc_id, url, end_index).

    Returns (None, None, 0) if sync should be disabled.
    """
    from gdoc.api.docs import get_docs_service
    from gdoc.api.drive import create_doc
    from gdoc.auth import get_credentials
    from gdoc.util import AuthError, extract_doc_id

    try:
        get_credentials()
    except AuthError:
        print("Error: Not authenticated with Google. Run `gdoc auth` first.")
        print("Or use --no-sync for local-only transcription.")
        sys.exit(1)

    if args.doc:
        # Append to existing document
        doc_id = extract_doc_id(args.doc)
        service = get_docs_service()
        doc = service.documents().get(documentId=doc_id).execute()
        title = doc.get("title", doc_id)
        url = f"https://docs.google.com/document/d/{doc_id}/edit"

        # Find current end index
        body = doc.get("body", {})
        content = body.get("content", [])
        end_index = content[-1].get("endIndex", 1) if content else 1

        # Insert a separator + session heading
        heading = f"\n---\n\n## Session — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [
                {"insertText": {"location": {"index": end_index - 1}, "text": heading}},
            ]},
        ).execute()
        end_index += len(heading) - 1

        print(f"Appending to: {title}")
        print(f"  {url}\n")
        return doc_id, url, end_index

    # Create a new document
    title = f"Meeting — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    speaker = config["speaker"]["name"]
    other = args.other_speaker or "Other"
    subtitle = f"{speaker} / {other}" if not args.mic_only else speaker

    result = create_doc(title, folder_id=args.folder)
    doc_id = result["id"]
    url = result["webViewLink"]

    # Insert heading
    heading = f"{title}\n{subtitle}\n\n"
    service = get_docs_service()
    service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": [
            {"insertText": {"location": {"index": 1}, "text": heading}},
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": 1, "endIndex": 1 + len(title) + 1},
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    "fields": "namedStyleType",
                },
            },
            {
                "updateParagraphStyle": {
                    "range": {
                        "startIndex": 1 + len(title) + 1,
                        "endIndex": 1 + len(title) + 1 + len(subtitle) + 1,
                    },
                    "paragraphStyle": {"namedStyleType": "SUBTITLE"},
                    "fields": "namedStyleType",
                },
            },
        ]},
    ).execute()

    end_index = 1 + len(heading)
    print(f"Created: {title}")
    print(f"  {url}\n")
    return doc_id, url, end_index


def main():
    parser = argparse.ArgumentParser(
        description="Real-time meeting transcription → Google Docs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  gquill                     Transcribe + sync to new Google Doc
  gquill --no-sync           Local-only (same as livekeet)
  gquill --with "Alice"      Label other speaker
  gquill --doc DOC_ID        Append to existing doc
  gquill --mic-only          Microphone only (no system audio)
        """,
    )

    # livekeet-compatible flags
    parser.add_argument(
        "-o", "--output",
        metavar="FILE",
        help="Local output file (default: from livekeet config)",
    )
    parser.add_argument(
        "--with", "-w",
        dest="other_speaker",
        metavar="NAME",
        help="Name of the other speaker",
    )
    parser.add_argument(
        "--mic-only", "-m",
        action="store_true",
        help="Only capture microphone (no system audio)",
    )
    parser.add_argument(
        "--multilingual",
        action="store_true",
        help="Use multilingual model",
    )
    parser.add_argument(
        "--model",
        choices=[
            "mlx-community/parakeet-tdt-0.6b-v2",
            "mlx-community/parakeet-tdt-0.6b-v3",
        ],
        help="Model to use",
    )
    parser.add_argument(
        "--device", "-d",
        help="Audio input device (number or name)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show periodic status updates",
    )

    # gquill flags
    parser.add_argument(
        "--doc",
        metavar="DOC_ID",
        help="Append to an existing Google Doc (ID or URL)",
    )
    parser.add_argument(
        "--folder",
        metavar="FOLDER_ID",
        help="Google Drive folder for new doc",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Disable Google Docs sync (local-only)",
    )

    args = parser.parse_args()

    # Load livekeet config
    config = load_config()

    # Resolve output path
    output_path = resolve_output_path(config, args.output)
    output_path, suffixed = ensure_unique_path(output_path)
    if suffixed:
        print(f"Output exists; saving to {output_path}")

    # Resolve device
    device = None
    if args.mic_only and args.device is not None:
        device, device_name = resolve_device(args.device)
        if device_name:
            print(f"Using input device: {device_name}")

    # Speaker names
    speaker_name = config["speaker"]["name"]
    other_name = args.other_speaker or "Other"

    # Model
    if args.multilingual:
        model = "mlx-community/parakeet-tdt-0.6b-v3"
    else:
        model = args.model or config["defaults"]["model"]

    system_audio = not args.mic_only

    # Set up Google Doc sync
    doc_sync = None
    if not args.no_sync:
        try:
            doc_id, url, end_index = _setup_doc(args, config)
            if doc_id:
                from gquill.doc_sync import DocSync
                doc_sync = DocSync(doc_id, end_index)
        except SystemExit:
            raise
        except Exception as e:
            print(f"Error: Could not set up Google Doc sync: {e}")
            print("Or use --no-sync for local-only transcription.")
            sys.exit(1)

    # Create and start transcriber
    from gquill.sync_transcriber import SyncTranscriber

    transcriber = SyncTranscriber(
        doc_sync=doc_sync,
        model_name=model,
        output_file=output_path,
        speaker_name=speaker_name,
        other_name=other_name,
        device=device,
        system_audio=system_audio,
        status_enabled=args.status,
    )
    transcriber.start()


if __name__ == "__main__":
    main()
