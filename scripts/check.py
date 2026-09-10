"""The canonical quality gate.

    python scripts/check.py

Runs every gate a change must pass, in order, stopping at the first failure.
This is the authority: the Makefile delegates to it and CI invokes it, so a
developer, `make check`, and GitHub Actions cannot drift apart.

It exists as a Python script rather than only a Makefile target because `make`
is not available on every supported development machine -- notably a stock
Windows install, which is where this repository is developed. Nothing here needs
Docker, a credential, or a network call.

Exit code is 0 when every gate passes, 1 otherwise.

    --list        print the gates and exit
    --only NAME   run one gate (repeatable)
    --skip NAME   skip one gate (repeatable)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

LINT_PATHS = ["app/", "tests/", "scripts/", "migrations/"]
# migrations/ is linted but never reformatted: an applied revision records what
# ran, and restyling it risks the replayable chain Phase 9 established.
FORMAT_PATHS = ["app/", "tests/", "scripts/"]
TYPECHECK_PATHS = ["app/", "scripts/", "tests/"]


@dataclass(frozen=True)
class Gate:
    name: str
    description: str
    command: list[str]


# `sys.executable -m` keeps every gate on the interpreter running this script,
# so a virtualenv is honoured without activating it.
PY = [sys.executable, "-m"]

GATES: tuple[Gate, ...] = (
    Gate("lint", "ruff lint over every first-party tree", [*PY, "ruff", "check", *LINT_PATHS]),
    Gate(
        "format",
        "ruff format check (no reformatting)",
        [*PY, "ruff", "format", "--check", *FORMAT_PATHS],
    ),
    Gate("typecheck", "mypy over app/, scripts/, tests/", [*PY, "mypy", *TYPECHECK_PATHS]),
    Gate("tests", "full pytest suite", [*PY, "pytest", "tests/", "-q"]),
    Gate(
        "eval-phase7",
        "Phase 7 guarded answer evaluation",
        [sys.executable, "scripts/evaluate_phase7_answers.py"],
    ),
    Gate(
        "eval-phase8",
        "Phase 8 multi-turn conversation evaluation",
        [sys.executable, "scripts/evaluate_phase8_conversations.py"],
    ),
)


def _run(gate: Gate) -> bool:
    print(f"\n=== {gate.name}: {gate.description} ===", flush=True)
    started = time.monotonic()
    # No shell: the argument list is passed through untouched, so nothing here
    # is interpreted by cmd.exe or /bin/sh.
    result = subprocess.run(gate.command, cwd=REPO_ROOT)
    elapsed = time.monotonic() - started
    status = "PASS" if result.returncode == 0 else f"FAIL (exit {result.returncode})"
    print(f"--- {gate.name}: {status} in {elapsed:.1f}s", flush=True)
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the canonical quality gates.")
    parser.add_argument("--list", action="store_true", help="print the gates and exit")
    parser.add_argument("--only", action="append", default=[], metavar="NAME")
    parser.add_argument("--skip", action="append", default=[], metavar="NAME")
    args = parser.parse_args(argv)

    names = [gate.name for gate in GATES]
    if args.list:
        for gate in GATES:
            print(f"{gate.name:12} {gate.description}")
        return 0

    for supplied in [*args.only, *args.skip]:
        if supplied not in names:
            parser.error(f"unknown gate {supplied!r}; choose from {', '.join(names)}")

    selected = [
        gate
        for gate in GATES
        if (not args.only or gate.name in args.only) and gate.name not in args.skip
    ]

    failed = []
    for gate in selected:
        if not _run(gate):
            failed.append(gate.name)
            break  # stop at the first failure

    print()
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print(f"All gates passed ({len(selected)}/{len(selected)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
