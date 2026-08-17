#!/usr/bin/env python3
"""CLI for the final Phase 7 guarded answer service.

Examples:
    python scripts/query_phase7_answer.py \
        --question "What brake complaints are reported for Ford F-150 2020?"
    python scripts/query_phase7_answer.py \
        --question "Are there official recalls affecting Ford F-150 2020?" --pretty
    python scripts/query_phase7_answer.py \
        --question "Did brake complaints cause the recall?" --include-trace
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from app.services.answer_synthesis.factory import build_guarded_answer_service
from app.services.answer_synthesis.guarded_models import MAX_QUESTION_CHARS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query Phase 7 guarded answer synthesis")
    parser.add_argument("--question", "-q", required=True, help="Vehicle-safety question")
    parser.add_argument(
        "--include-trace",
        action="store_true",
        help="Include sanitized Phase 7 validation trace",
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

    question = (args.question or "").strip()
    if not question:
        print("error: question cannot be empty", file=err)
        return 2
    if len(question) > MAX_QUESTION_CHARS:
        print(f"error: question must be at most {MAX_QUESTION_CHARS} characters", file=err)
        return 2

    factory = service_factory or build_guarded_answer_service
    try:
        service = factory()
    except Exception:
        print("error: guarded answer service is unavailable", file=err)
        return 2

    try:
        payload = service.answer(question).to_dict()
    except Exception:
        print("error: guarded answer synthesis failed", file=err)
        return 1

    if not args.include_trace:
        payload.pop("trace", None)

    if args.pretty:
        rendered = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    else:
        rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    print(rendered, file=out)
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
