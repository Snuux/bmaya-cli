import pytest
from bmaya_bridge import skinweights
from bmaya_bridge.errors import BridgeError

NAMES = ["root", "arm", "hand"]


def test_flat_weights_become_rows_and_back() -> None:
    flat = [1.0, 0.0, 0.0, 0.0, 0.25, 0.75]
    rows = skinweights.rows_from_flat(flat, NAMES)
    assert rows == [{"root": 1.0}, {"arm": 0.25, "hand": 0.75}]
    assert skinweights.flat_from_rows(rows, NAMES) == flat


def test_survey_counts_what_breaks_a_game_skin() -> None:
    rows = [
        {"root": 0.4, "arm": 0.3, "hand": 0.3},
        {"root": 0.5, "arm": 0.2},
        {},
        {"arm": 1.0},
    ]
    found = skinweights.survey(rows, NAMES + ["tip"], 2)
    assert found.over == [0]
    assert found.unnormalized == [1]
    assert found.unweighted == [2]
    assert found.most == 3
    assert found.unused == ["tip"]
    assert found.influences[0] == {"name": "arm", "verts": 3, "max": 1.0}
    assert skinweights.issue_lines(found, 2) == [
        "1 verts with more than 2 influences",
        "1 verts whose weights do not add up to 1",
        "1 unweighted verts",
    ]
    assert skinweights.samples(found) == {"over": [0], "unnormalized": [1], "unweighted": [2]}


def test_rounding_in_a_file_is_not_called_unnormalized() -> None:
    found = skinweights.survey([{"root": 0.3334, "arm": 0.3333, "hand": 0.3332}], NAMES, 4)
    assert found.unnormalized == []


def test_finish_prunes_caps_and_normalizes_only_the_touched_rows() -> None:
    before = [{"root": 1.0}, {"root": 1.0}]
    after = [
        {"root": 0.5, "arm": 0.3, "hand": 0.195, "tip": 0.005},
        {"root": 0.7, "arm": 0.7},
    ]
    done = skinweights.finish(before, after, [0], prune=0.01, most=2)
    assert done.rows[0] == pytest.approx({"root": 0.5 / 0.8, "arm": 0.3 / 0.8})
    assert done.rows[1] == {"root": 0.7, "arm": 0.7}
    assert (done.pruned, done.capped, done.normalized) == (1, 1, 1)
    assert done.changed == 2


def test_pruning_keeps_the_largest_weight_of_a_faint_vertex() -> None:
    assert skinweights.pruned_row({"root": 0.004, "arm": 0.002}, 0.01) == {"root": 0.004}


def test_nothing_is_cut_without_a_budget() -> None:
    row = {"a": 0.2, "b": 0.2, "c": 0.2, "d": 0.2, "e": 0.2}
    done = skinweights.finish([row], [row], [0], prune=None, most=None)
    assert done.rows == [row]
    assert done.changed == 0


def test_blend_mixes_triangle_corners_by_their_shares() -> None:
    mixed = skinweights.blend([({"root": 1.0}, 0.5), ({"arm": 1.0}, 0.25), ({"arm": 1.0}, 0.25)])
    assert mixed == {"root": 0.5, "arm": 0.5}


@pytest.mark.parametrize(
    ("name", "twin"),
    [
        ("hand_r", "hand_l"),
        ("hand_L", "hand_R"),
        ("L_hand", "R_hand"),
        ("Bip01 L Hand", "Bip01 R Hand"),
        ("mixamorig:LeftArm", "mixamorig:RightArm"),
        ("leftFoot", "rightFoot"),
        ("arm_left_01", "arm_right_01"),
        ("spine_01", "spine_01"),
        ("Leftover", "Leftover"),
        ("root", "root"),
    ],
)
def test_joints_pair_across_sides_by_name(name: str, twin: str) -> None:
    assert skinweights.other_side(name) == twin
    assert skinweights.other_side(twin) == name


def test_a_mirrored_row_moves_weights_to_the_other_side() -> None:
    assert skinweights.mirrored_row({"arm_r": 0.6, "spine": 0.4}) == {"arm_l": 0.6, "spine": 0.4}


def test_rename_takes_a_pattern_and_pairs() -> None:
    rename = skinweights.renamer("{}_r, arm=forearm_r")
    assert rename("hand") == "hand_r"
    assert rename("arm") == "forearm_r"
    assert skinweights.renamed_row({"hand": 0.5, "arm": 0.5}, rename) == {
        "hand_r": 0.5,
        "forearm_r": 0.5,
    }
    assert skinweights.renamer("")("hand") == "hand"


@pytest.mark.parametrize("spec", ["hand", "=x", "{}_r,{}_l"])
def test_bad_rename_says_how_to_write_it(spec: str) -> None:
    with pytest.raises(BridgeError) as error:
        skinweights.renamer(spec)
    assert error.value.hint


def test_a_weights_file_can_be_a_list_or_hold_one_under_weights() -> None:
    rows = [{"root": 1}, {"arm": 0.5, "hand": 0.5}]
    assert skinweights.read_rows(rows, 2) == {0: {"root": 1.0}, 1: {"arm": 0.5, "hand": 0.5}}
    held = {"bones": [], "weights": rows, "rest_verts": []}
    assert skinweights.read_rows(held, 2) == skinweights.read_rows(rows, 2)


def test_a_weights_file_can_set_only_some_vertices() -> None:
    assert skinweights.read_rows({"weights": {"7": {"arm": 1}}}, 10) == {7: {"arm": 1.0}}


@pytest.mark.parametrize(
    ("data", "words"),
    [
        ([{"root": 1}], "weights for 1 verts; the mesh has 2"),
        ({"bones": []}, "no 'weights'"),
        ("text", "holds no weights"),
        ([{"root": "x"}, {}], "not a number"),
        ([{"root": -1}, {}], "is -1"),
        ([[1.0], {}], "not a {joint: weight}"),
        ({"weights": {"9": {}}}, "Vertex 9 is not in the mesh"),
        ({"weights": {"a": {}}}, "'a' is not a vertex number"),
    ],
)
def test_a_bad_weights_file_says_what_is_wrong(data: object, words: str) -> None:
    with pytest.raises(BridgeError) as error:
        skinweights.read_rows(data, 2)
    assert words in error.value.message
    assert error.value.hint


def test_vertex_specs_pick_numbers_and_ranges() -> None:
    assert skinweights.vertex_ids("0:4:2, 9, 3", 20) == [0, 2, 3, 4, 9]
    assert skinweights.vertex_ids("18:40", 20) == [18, 19]


@pytest.mark.parametrize("spec", ["a", "5:2", "1:2:0", "30", ","])
def test_bad_vertex_specs_are_refused(spec: str) -> None:
    with pytest.raises(BridgeError):
        skinweights.vertex_ids(spec, 20)


def test_rounded_rows_sort_by_weight_and_drop_zeros() -> None:
    row = skinweights.rounded_row({"a": 0.1234567, "b": 0.8765433, "c": 1e-9})
    assert list(row.items()) == [("b", 0.876543), ("a", 0.123457)]


def test_heat_runs_from_blue_to_red() -> None:
    assert skinweights.heat(0.0) == skinweights.HEAT[0][1]
    assert skinweights.heat(1.0) == skinweights.HEAT[-1][1]
    assert skinweights.heat(2.0) == skinweights.HEAT[-1][1]
    low, high = skinweights.heat(0.1), skinweights.heat(0.9)
    assert low[2] > low[0]
    assert high[0] > high[2]


def test_influence_colors_mix_by_weight() -> None:
    colors = {"a": (200, 0, 0), "b": (0, 0, 200)}
    assert skinweights.mixed_color({"a": 0.5, "b": 0.5}, colors) == (100, 0, 100)
    assert skinweights.mixed_color({}, colors) == skinweights.NO_SKIN
