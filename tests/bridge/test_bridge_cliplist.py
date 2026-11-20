from bmaya_bridge import cliplist
from bmaya_bridge.cliplist import Keys, Mark, Row

NODE = "gameExporter_fp_clips"


def rows_and_marks() -> tuple[list[Row], list[Mark]]:
    rows = [
        Row("fp_idle", 0, 90, NODE, 0, True),
        Row("fp_walk", 290, 342, NODE, 1, True),
        Row("fp_fall", 780, 781, NODE, 3, False),
    ]
    marks = [
        Mark("fp_idle", 0, 90, "bookmark_fp_idle"),
        Mark("fp_walk", 290, 340, "bookmark_fp_walk"),
        Mark("fp_old", 20000, 20090, "bookmark_fp_old"),
    ]
    return rows, marks


def test_merge_joins_rows_and_bookmarks_by_name_in_time_order() -> None:
    rows, marks = rows_and_marks()
    clips = cliplist.merge(rows, marks)
    assert [clip.name for clip in clips] == ["fp_idle", "fp_walk", "fp_fall", "fp_old"]
    walk = clips[1]
    assert (walk.start, walk.end) == (290, 342)
    assert [mark.node for mark in walk.marks] == ["bookmark_fp_walk"]
    assert clips[3].rows == [] and (clips[3].start, clips[3].end) == (20000, 20090)


def test_problems_name_what_is_off() -> None:
    rows, marks = rows_and_marks()
    rows.append(Row("fp_run", 330, 360, NODE, 2, True))
    rows.append(Row("fp_kick", 900, 930, "gameExporter_model", 0, True, "model"))
    rows.append(Row("fp_idle", 5, 95, NODE, 4, True))
    clips = cliplist.merge(rows, marks)
    keys = Keys([0.0, 30.0, 90.0, 300.0, 335.0, 910.0, 20010.0])
    found = [problem.text for problem in cliplist.problems(clips, keys)]
    assert "fp_walk: the bookmark spans 290:340, the Game Exporter row 290:342" in found
    assert "fp_old: a bookmark with no Game Exporter row, so it is not exported" in found
    assert f"fp_fall: export is off in {NODE}, so it is not exported" in found
    assert "fp_fall has no keys in 780:781" in found
    assert "fp_walk and fp_run overlap at 330:342" in found
    assert "fp_kick: its row is in gameExporter_model, which exports a model, not clips" in found
    assert "fp_idle has Game Exporter rows with other ranges: 0:90, 5:95" in found
    overlap = [p for p in cliplist.problems(clips, keys) if p.text.startswith("fp_walk and")]
    assert overlap[0].clips == ("fp_walk", "fp_run")


def test_bookmarks_alone_and_float32_ranges_are_no_complaint() -> None:
    clips = cliplist.merge([], [Mark("walk", 0, 30, "bm_walk"), Mark("run", 30, 50, "bm_run")])
    assert cliplist.problems(clips, Keys([0.0, 30.0, 50.0])) == []
    row = Row("hop", 10.1, 20.3, NODE, 0, True)
    float32 = cliplist.merge([row], [Mark("hop", 10.1000004, 20.2999992, "bm")])
    assert cliplist.problems(float32, Keys([15.0])) == []


def test_keys_count_once_per_time() -> None:
    keys = Keys([1.0, 1.0, 2.0, 5.0, 5.0, 5.0])
    assert len(keys) == 6 and keys.within(1, 2) == 3 and keys.within(2.5, 10) == 3
    assert keys.within(6, 9) == 0


def test_keys_outside_clips_split_into_padding_and_strays() -> None:
    rows, marks = rows_and_marks()
    clips = cliplist.merge(rows[:2], marks[:2])
    keys = Keys([-30.0, 0.0, 90.0, 120.0, 264.0, 290.0, 300.0, 342.0, 361.0, 5000.0, 5020.0])
    spans = cliplist.outside(clips, keys)
    assert [(item.start, item.end, item.keys) for item in spans] == [
        (-30.0, -30.0, 1),
        (120.0, 120.0, 1),
        (264.0, 264.0, 1),
        (361.0, 361.0, 1),
        (5000.0, 5020.0, 2),
    ]
    assert (spans[1].near, spans[1].gap) == ("fp_idle", 30.0)
    assert [(item.start, item.near) for item in cliplist.stray(spans)] == [(5000.0, "fp_walk")]
    assert [(item.start, item.end) for item in cliplist.padding(spans, "fp_idle")] == [
        (-30.0, -30.0),
        (120.0, 120.0),
    ]
    assert "2 keys at 5000:5020 belong to no clip" in cliplist.stray_text(cliplist.stray(spans))


def test_padding_on_both_sides_of_a_clip_stays_apart() -> None:
    clips = cliplist.merge([Row("walk", 290, 342, NODE, 0, True)], [])
    spans = cliplist.outside(clips, Keys([280.0, 300.0, 350.0]))
    assert [(item.start, item.end, item.gap) for item in spans] == [
        (280.0, 280.0, 10.0),
        (350.0, 350.0, 8.0),
    ]


def test_keys_of_a_removed_clip_next_to_another_are_far() -> None:
    clips = cliplist.merge([Row("walk", 20120, 20172, NODE, 0, True)], [])
    spans = cliplist.outside(clips, Keys([19955.0, 19970.0, 20107.0, 20113.0]))
    assert [(item.start, item.end, item.far) for item in spans] == [
        (19955.0, 19970.0, True),
        (20107.0, 20113.0, False),
    ]


def test_no_clips_makes_every_key_far_without_a_gap() -> None:
    spans = cliplist.outside([], Keys([1.0, 2.0]))
    assert [(item.near, item.far, item.keys) for item in spans] == [("", True, 2)]


def test_free_keeps_frames_other_clips_cover() -> None:
    clips = cliplist.merge([Row("a", 0, 30, NODE, 0, True), Row("b", 30, 60, NODE, 1, True)], [])
    pieces = cliplist.free(0, 30, [clips[1]])
    assert len(pieces) == 1 and pieces[0][0] == 0 and 29.99 < pieces[0][1] < 30


def test_next_start_leaves_a_gap_after_the_last_clip_and_its_padding() -> None:
    rows, marks = rows_and_marks()
    clips = cliplist.merge(rows, marks[:2])
    assert cliplist.next_start(clips, 200, 0, []) == 990
    assert cliplist.next_start(clips, 20, 0, []) == 810
    walk = cliplist.merge(rows[:2], [])
    padded = cliplist.outside(walk, Keys([360.0, 365.0]))
    assert cliplist.next_start(walk, 20, 0, padded) == 370
    assert cliplist.next_start(walk, 200, 0, padded) == 550
    assert cliplist.next_start([], 20, 1, []) == 1
    assert cliplist.span(2.5, 10.0) == "2.5:10"
