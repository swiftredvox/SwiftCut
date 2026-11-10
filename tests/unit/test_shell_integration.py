"""Registry round-trip against a throwaway key (never the real Explorer location)."""
import sys

import pytest

from swiftcut.core import shell_integration as si

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
BASE = r"Software\SwiftCutTests\SystemFileAssociations"


@pytest.fixture
def clean():
    yield
    si.disable(BASE)
    import winreg
    for sub in (BASE, r"Software\SwiftCutTests"):
        try:
            # Remove any now-empty .ext parents, then the test roots.
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BASE) as k:
                names = []
                i = 0
                while True:
                    try:
                        names.append(winreg.EnumKey(k, i))
                        i += 1
                    except OSError:
                        break
            for n in names:
                for leaf in (rf"{BASE}\{n}\shell", rf"{BASE}\{n}"):
                    try:
                        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, leaf)
                    except OSError:
                        pass
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            pass


def test_enable_disable_roundtrip(clean):
    import winreg
    assert not si.is_enabled(BASE)
    si.enable(BASE)
    assert si.is_enabled(BASE)
    cmd = si.registered_command(BASE)
    assert cmd == si.launch_command() and cmd.endswith('"%1"')
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{BASE}\.mkv\shell\{si.VERB_KEY}") as k:
        assert winreg.QueryValueEx(k, "MUIVerb")[0] == si.VERB_TEXT
    si.disable(BASE)
    assert not si.is_enabled(BASE)
    for ext in si.VIDEO_EXTENSIONS:
        with pytest.raises(OSError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{BASE}\.{ext}\shell\{si.VERB_KEY}")


def test_disable_when_not_enabled_is_harmless(clean):
    si.disable(BASE)
    assert not si.is_enabled(BASE)


def test_launch_command_points_at_launcher():
    cmd = si.launch_command()
    assert "SwiftCut.pyw" in cmd or getattr(sys, "frozen", False)
