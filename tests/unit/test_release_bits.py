"""Small release-facing behaviours: CLI switches and privacy of the diagnostic summary."""
import getpass
import os

from swiftcut import ISSUES_URL, RELEASES_URL, __version__
from swiftcut.app import _cli


def test_version_switch(capsys):
    assert _cli(["SwiftCut.exe", "--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_normal_launch_is_not_a_cli_command():
    assert _cli(["SwiftCut.exe", "video.mp4"]) is None


def test_diagnostics_contain_no_personal_paths():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from swiftcut import diagnostics
    text = diagnostics.summary() + diagnostics.report_url()
    assert __version__ in text
    for private in (getpass.getuser(), os.path.expanduser("~"), ":\\"):
        assert private.lower() not in text.lower(), f"diagnostics leak {private!r}"


def test_links_point_at_the_project():
    assert RELEASES_URL == "https://github.com/swiftredvox/SwiftCut/releases"
    assert ISSUES_URL.endswith("/swiftredvox/SwiftCut/issues")
