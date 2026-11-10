import pytest

from swiftcut.core.timecode import FrameGrid
from swiftcut.ui.trim_model import TrimModel


@pytest.fixture
def model():
    m = TrimModel()
    m.load(FrameGrid.uniform(10, 3.0))   # 30 frames, 0.1s apart
    return m


def test_initial_selection_is_whole_video(model):
    assert model.start_time == 0
    assert model.end_time == pytest.approx(3.0)
    assert model.is_full
    assert model.frame_count == 30


def test_start_cannot_pass_end(model):
    model.set_end_index(10)
    model.set_start_index(20)
    assert model.start_idx == 10
    assert model.frame_count == 1


def test_end_cannot_precede_start(model):
    model.set_start_index(15)
    model.set_end_index(3)
    assert model.end_idx == 15


def test_indices_are_clamped(model):
    model.set_start_index(-5)
    model.set_end_index(999)
    assert (model.start_idx, model.end_idx) == (0, 29)


def test_time_snapping(model):
    model.set_start_time(0.54)
    assert model.start_time == pytest.approx(0.5)
    model.set_end_time(1.26)             # nearest boundary 1.3 -> last kept frame starts at 1.2
    assert model.end_time == pytest.approx(1.3)
    assert model.end_idx == 12
    model.set_end_time(2.99)
    assert model.end_idx == 29


def test_reset(model):
    model.set_start_index(5)
    model.reset()
    assert model.is_full


def test_replace_grid_keeps_selection_times(model):
    model.set_start_index(10)
    model.set_end_index(19)
    exact = FrameGrid([i * 0.1 + 0.001 for i in range(30)], 3.0, exact=True)
    model.replace_grid(exact)
    assert model.start_time == pytest.approx(1.001)
    assert model.end_time == pytest.approx(2.001)


def test_replace_grid_full_selection_stays_full(model):
    model.replace_grid(FrameGrid([i * 0.1 for i in range(31)], 3.1, exact=True))
    assert model.is_full and model.end_idx == 30


def test_changed_signal(model):
    hits = []
    model.changed.connect(lambda: hits.append(1))
    model.set_start_index(3)
    model.set_start_index(3)   # no-op shouldn't emit
    assert hits == [1]


def test_undo_redo(model):
    model.set_start_index(5)
    model.commit()
    model.set_end_index(20)
    model.commit()
    assert model.undo() and (model.start_idx, model.end_idx) == (5, 29)
    assert model.undo() and model.is_full
    assert not model.undo()
    assert model.redo() and (model.start_idx, model.end_idx) == (5, 29)
    assert model.redo() and (model.start_idx, model.end_idx) == (5, 20)
    assert not model.redo()


def test_uncommitted_change_is_undoable(model):
    model.set_start_index(7)               # not yet committed (e.g. mid-burst)
    assert model.can_undo()
    assert model.undo() and model.is_full
    assert model.redo() and model.start_idx == 7


def test_new_edit_clears_redo(model):
    model.set_start_index(5); model.commit()
    model.undo()
    model.set_end_index(10); model.commit()
    assert not model.can_redo()


def test_grid_replacement_is_not_an_edit(model):
    model.replace_grid(FrameGrid([i * 0.1 for i in range(30)], 3.0, exact=True))
    model.commit()
    assert not model.can_undo()


def test_history_survives_grid_replacement(model):
    model.set_start_index(10); model.commit()
    model.replace_grid(FrameGrid([i * 0.1 + 0.001 for i in range(30)], 3.0, exact=True))
    assert model.undo() and model.is_full


def test_loading_clears_history(model):
    model.set_start_index(10); model.commit()
    model.load(FrameGrid.uniform(10, 3.0))
    assert not model.can_undo() and not model.can_redo()
