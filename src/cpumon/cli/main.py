"""CLI orchestration for cpumon.

The runtime guard runs as the first executable step, before any other project
import is resolved inside :func:`main`.
"""

from __future__ import annotations

import sys

from cpumon.cli.python_check import ensure_supported_python


def main(argv: list[str] | None = None) -> int:
    """Entry point for the cpumon command."""
    ensure_supported_python()

    from cpumon.backend import create_backend
    from cpumon.cli.args import build_parser
    from cpumon.cli.diagnostics import probe, selftest
    from cpumon.cli.exit_codes import ExitCode
    from cpumon.core.errors import PlatformError
    from cpumon.runtime.app import Application

    args = build_parser().parse_args(argv)

    try:
        if args.probe:
            return probe()
        if args.selftest:
            return selftest(args.selftest[0], args.selftest[1], args.interval)
        return Application(create_backend(), args.interval).run()
    except PlatformError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return int(ExitCode.ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
