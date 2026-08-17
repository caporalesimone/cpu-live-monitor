"""Print one release's section of CHANGELOG.md, using the standard library only.

    python tools/release_notes.py 1.0.1
    python tools/release_notes.py 1.0.1 --output notes.md

The release workflow feeds the result to ``gh release create --notes-file``, so
the changelog is the single source of truth for what a release says: the notes
are written once, reviewed in the pull request, and published verbatim.

No custom markers are needed. ``## [1.0.1] - 2026-08-18`` is already an
unambiguous delimiter — it is the Keep a Changelog format the file follows — and
a second convention layered on top would be one more thing to keep in step.

A missing section is an error, not an empty release: the workflow stops before
publishing anything.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

# Spelled out rather than taken from __doc__: docstrings are stripped under
# python -OO, which would make the help text vanish or the tool crash.
DESCRIPTION = "print one version's section of the changelog"

# "## [1.0.1] - 2026-08-18", "## 1.0.1", "## [Unreleased]" — the brackets and
# the date are both optional, and neither belongs to the version.
HEADING = re.compile(r"^##\s+\[?(?P<version>[^\]\s]+)\]?")

# "[1.0.1]: https://github.com/..." — the link definitions live at the foot of
# the file, inside the last section as far as a line scanner can tell.
LINK_DEFINITION = re.compile(r"^\[[^\]]+\]:\s")


def extract(changelog: str, version: str) -> str:
    """The body of *version*'s section, without its heading or the link defs."""
    body: list[str] = []
    inside = False
    for line in changelog.splitlines():
        heading = HEADING.match(line)
        if heading:
            if inside:
                break
            inside = heading["version"].lstrip("v") == version.lstrip("v")
            continue
        if inside and not LINK_DEFINITION.match(line):
            body.append(line)
    if not inside:
        raise LookupError(f"no section for {version} in the changelog")
    return "\n".join(body).strip()


def main() -> int:
    parser = argparse.ArgumentParser(prog="release_notes", description=DESCRIPTION)
    parser.add_argument("version", help="the version to extract, with or without a leading v")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=CHANGELOG,
        help="the file to read (default: CHANGELOG.md)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="write the notes here instead of to stdout",
    )
    args = parser.parse_args()

    try:
        notes = extract(args.changelog.read_text(encoding="utf-8"), args.version)
    except (LookupError, OSError) as exc:
        print(f"release_notes: {exc}", file=sys.stderr)
        return 1
    if not notes:
        print(f"release_notes: the section for {args.version} is empty", file=sys.stderr)
        return 1

    if args.output:
        args.output.write_text(notes + "\n", encoding="utf-8")
    else:
        # UTF-8 whatever the console claims: the changelog is not pure ASCII and
        # a redirected stdout on Windows would otherwise pick the locale codec.
        sys.stdout.buffer.write((notes + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
