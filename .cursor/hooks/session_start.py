"""Cursor sessionStart hook -- writes the heartbeat that proves hooks fired.

Cloud agents never fire this event (nor sessionEnd); their heartbeat comes
from stop. See _common's module docstring.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import safe_main


if __name__ == "__main__":
    raise SystemExit(safe_main("sessionStart"))
