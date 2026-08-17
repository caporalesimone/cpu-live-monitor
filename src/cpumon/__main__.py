"""Launch cpumon via ``python -m cpumon``."""

from __future__ import annotations

from cpumon.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
