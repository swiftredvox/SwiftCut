"""A second SwiftCut launch hands its file to the first, exactly as Explorer would trigger it."""
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SENDER = r'''
import sys
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QCoreApplication
from swiftcut.single_instance import send_to_running
app = QCoreApplication([])
print("sent" if send_to_running({"open": sys.argv[3]}, name=sys.argv[2], timeout_ms=3000) else "none")
'''


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _send(name: str, path: str) -> subprocess.Popen:
    """Launch a separate process that plays the role of the second SwiftCut launch."""
    return subprocess.Popen([sys.executable, "-c", SENDER, str(ROOT), name, path],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")


def test_second_launch_hands_file_to_first(qapp):
    from swiftcut.single_instance import InstanceServer
    name = f"SwiftCutTest-{uuid.uuid4().hex[:8]}"

    proc = _send(name, "nobody-listening.mp4")
    assert proc.communicate(timeout=30)[0].strip() == "none"     # no instance running yet

    server = InstanceServer(name)
    assert server.listen()
    got = []
    server.message.connect(got.append)
    path = "C:\\Videos\\clip (1) \u00e9.mp4"
    proc = _send(name, path)
    end = time.time() + 20
    while time.time() < end and (not got or proc.poll() is None):
        qapp.processEvents()
        time.sleep(0.01)
    assert proc.communicate(timeout=10)[0].strip() == "sent"
    assert got == [{"open": path}]
    server.close()
