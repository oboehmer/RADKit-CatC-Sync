"""Entry point for python -m radkit_catc_sync."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
