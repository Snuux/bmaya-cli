import pytest
from bmaya_bridge import undoable


def test_the_plugin_ships_where_the_bridge_looks_for_it() -> None:
    assert undoable.plugin_path().is_file()


def test_a_step_is_taken_once() -> None:
    done: list[str] = []
    undoable.put(undoable.Step(lambda: done.append("do"), lambda: done.append("undo")))
    step = undoable.take()
    step.do()
    step.undo()
    assert done == ["do", "undo"]
    with pytest.raises(RuntimeError, match="bmaya bridge"):
        undoable.take()
