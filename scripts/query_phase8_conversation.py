#!/usr/bin/env python3
"""CLI for the Phase 8 guarded multi-turn conversation service.

Each `--question` is one conversational turn, answered in order through the
full Phase 7 guarded path. Follow-up questions inherit only allowlisted
entity slots from earlier turns; no prior conversation text is forwarded.

Examples:
    python scripts/query_phase8_conversation.py \
        -q "What brake complaints are reported for Ford F-150 2020?" \
        -q "What about recalls?" --pretty
    python scripts/query_phase8_conversation.py \
        --conversation-id 0b7f... -q "What about recalls?"
    python scripts/query_phase8_conversation.py \
        -q "Are there recalls for Ford F-150 2020?" --delete-when-done
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from app.services.conversation.factory import build_conversation_service
from app.services.conversation.models import (
    MAX_QUESTION_CHARS,
    MAX_TITLE_CHARS,
    ConversationLimitError,
    ConversationNotFoundError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query Phase 8 guarded multi-turn conversation")
    parser.add_argument(
        "--question",
        "-q",
        action="append",
        required=True,
        help="Conversational turn; repeat for a multi-turn conversation",
    )
    parser.add_argument(
        "--conversation-id",
        help="Continue an existing conversation instead of starting a new one",
    )
    parser.add_argument("--title", help="Optional title for a new conversation")
    parser.add_argument(
        "--include-trace",
        action="store_true",
        help="Include the sanitized Phase 7 validation trace",
    )
    parser.add_argument(
        "--delete-when-done",
        action="store_true",
        help="Hard-delete the conversation after the final turn",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    service_factory: Callable | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run CLI and return process exit code. Abstention remains a success."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = build_parser().parse_args(argv)

    questions = [(q or "").strip() for q in (args.question or [])]
    if not questions or not all(questions):
        print("error: every question must be non-empty", file=err)
        return 2
    if any(len(q) > MAX_QUESTION_CHARS for q in questions):
        print(f"error: each question must be at most {MAX_QUESTION_CHARS} characters", file=err)
        return 2

    factory = service_factory or build_conversation_service
    try:
        service = factory()
    except Exception:
        print("error: conversation service is unavailable", file=err)
        return 2

    conversation_id = args.conversation_id
    try:
        if not conversation_id:
            title = (args.title or "").strip()[:MAX_TITLE_CHARS] or None
            conversation_id = service.start_conversation(title).conversation_id
    except Exception:
        print("error: could not create conversation", file=err)
        return 1

    turns: list[dict] = []
    for question in questions:
        try:
            result = service.answer(conversation_id, question)
        except ConversationNotFoundError:
            print("error: conversation not found", file=err)
            return 2
        except ConversationLimitError:
            print("error: conversation reached its bounded turn limit", file=err)
            return 2
        except Exception:
            print("error: conversation turn failed", file=err)
            return 1

        payload = result.to_dict()
        if not args.include_trace:
            payload["guarded_answer"]["trace"] = None
        turns.append(payload)

    deleted = False
    if args.delete_when_done:
        try:
            deleted = service.delete_conversation(conversation_id)
        except Exception:
            print("error: could not delete conversation", file=err)
            return 1

    document = {
        "conversation_id": conversation_id,
        "turns": turns,
        "turn_count": len(turns),
        "deleted": deleted,
        "phase": "phase_8",
    }

    if args.pretty:
        rendered = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True)
    else:
        rendered = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    print(rendered, file=out)
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
