"""Windowless launcher (pythonw). Used by the shortcut, the Explorer menu and run.bat."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from swiftcut.app import main  # noqa: E402

sys.exit(main())
