"""Allow `python -m ppt` invocation.

Exit codes (§5):
  0 — normal
  1 — business error (input, prices, OSS, unknown command)
  130 — Ctrl+C interrupt
"""

import sys

from ppt.cli import main
from ppt import ensure_logging

if __name__ == "__main__":
    ensure_logging()
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except SystemExit as e:
        sys.exit(e.code if e.code is not None else 1)
    except Exception:
        sys.exit(1)
    else:
        sys.exit(0)
