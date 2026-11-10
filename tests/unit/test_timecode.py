import pytest

from swiftcut.core.timecode import FrameGrid, format_time, parse_time


@pytest.mark.parametrize("t,expected", [
    (0, "0:00.000"), (7.25, "0:07.250"), (59.9995, "1:00.000"), (61.004, "1:01.004"),
    (3723.004, "1:02:03.004"), (-1, "0:00.000"),
])
def test_format_time(t, expected):
    assert format_time(t) == expected


def test_format_time_without_ms_and_forced_hours():
    assert format_time(75.9, show_ms=False) == "1:15"
    assert format_time(5, force_hours=True) == "0:00:05.000"


@pytest.mark.parametrize("text,expected", [
    ("75", 75.0), ("75.5", 75.5), ("75,5", 75.5), ("75.5s", 75.5), ("1:15", 75.0),
    ("1:15.250", 75.25), ("0:01:15.5", 75.5), ("1:02:03.004", 3723.004), (" 0:07.250 ", 7.25),
])
def test_parse_time(text, expected):
    assert parse_time(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["", "abc", "1:75", "1:61:00", "1::2", "-3", "1.2.3"])
def test_parse_time_rejects(bad):
    with pytest.raises(ValueError):
        parse_time(bad)


def test_uniform_grid():
    g = FrameGrid.uniform(30, 2.0)
    assert len(g) == 60
    assert g.times[1] == pytest.approx(1 / 30)
    assert g.end_time == pytest.approx(2.0)
    assert not g.exact


def test_uniform_grid_bad_fps_falls_back():
    assert len(FrameGrid.uniform(0, 1.0)) == 30


def test_index_lookup():
    g = FrameGrid([0.0, 0.1, 0.2, 0.35], duration=0.45, exact=True)
    assert g.index_at(0.0) == 0
    assert g.index_at(0.099) == 0
    assert g.index_at(0.1) == 1
    assert g.index_at(10) == 3
    assert g.index_at(-1) == 0
    assert g.nearest_index(0.26) == 2
    assert g.nearest_index(0.30) == 3


def test_frame_end_and_last_frame():
    g = FrameGrid([0.0, 0.1, 0.2], duration=0.3, exact=True)
    assert g.frame_end(0) == pytest.approx(0.1)
    assert g.frame_end(2) == pytest.approx(0.3)            # bounded by container duration
    g2 = FrameGrid([0.0, 0.1, 0.2], duration=0.2, exact=True)
    assert g2.frame_end(2) == pytest.approx(0.3)           # unknown: one typical interval


def test_vfr_min_interval():
    g = FrameGrid([0.0, 0.033, 0.1, 0.105, 0.2], duration=0.25, exact=True)
    assert g.min_interval == pytest.approx(0.005)


def test_keyframe_lookup():
    g = FrameGrid([i / 10 for i in range(30)], 3.0, True, keyframes=[0.0, 1.0, 2.0])
    assert g.keyframe_at_or_before(0.5) == 0.0
    assert g.keyframe_at_or_before(1.0) == 1.0
    assert g.keyframe_at_or_before(1.99) == 1.0
    assert g.keyframe_at_or_before(2.5) == 2.0
