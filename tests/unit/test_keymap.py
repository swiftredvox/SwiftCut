import pytest
from PySide6.QtCore import QSettings

from swiftcut.ui.keymap import ACTIONS, Keymap, display, normalise


@pytest.fixture
def qs(tmp_path):
    return QSettings(str(tmp_path / "keys.ini"), QSettings.Format.IniFormat)


def test_wsad_defaults_present_alongside_classic_keys():
    km = Keymap()
    expected = {
        "frame_back": ["Left", "A"], "frame_fwd": ["Right", "D"],
        "back_1s": ["Shift+Left", "Shift+A"], "fwd_1s": ["Shift+Right", "Shift+D"],
        "back_10s": ["Ctrl+Left", "Ctrl+A"], "fwd_10s": ["Ctrl+Right", "Ctrl+D"],
        "mark_in": ["I", "[", "Q"], "mark_out": ["O", "]", "E"],
        "loop": ["L", "W"], "audio": ["M", "S"],
    }
    for aid, keys in expected.items():
        assert km.keys(aid) == keys


def test_defaults_have_no_duplicate_keys():
    seen = {}
    for a in ACTIONS:
        for k in a.defaults:
            assert normalise(k) not in seen, f"{k} used by {seen.get(normalise(k))} and {a.id}"
            seen[normalise(k)] = a.id


def test_reassigning_a_key_moves_it(qs):
    km = Keymap(qs)
    lost = km.set_keys("reset", ["R", "A"])
    assert lost == ["frame_back"]
    assert km.keys("frame_back") == ["Left"]
    assert km.owner("A") == "reset"


def test_changes_persist_and_reset(qs):
    km = Keymap(qs)
    km.set_keys("loop", ["F"])
    assert Keymap(qs).keys("loop") == ["F"]
    assert not Keymap(qs).is_default()
    km.reset()
    assert Keymap(qs).keys("loop") == ["L", "W"]
    assert Keymap(qs).is_default()


def test_only_differences_are_stored(qs):
    km = Keymap(qs)
    km.set_keys("loop", ["L", "W"])
    qs.sync()
    assert qs.value("keys/loop") is None


def test_max_three_keys_and_normalising():
    km = Keymap()
    km.set_keys("reset", ["r", "shift+r", "ctrl+r", "alt+r"])
    assert km.keys("reset") == ["R", "Shift+R", "Ctrl+R"]


def test_display_uses_arrows():
    assert display("Left") == "←"
    assert display("Shift+Right").endswith("→")


def test_hint():
    assert Keymap().hint("frame_back") == "← or A"
