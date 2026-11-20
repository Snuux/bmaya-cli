import math

from bmaya_bridge import skeleton
from bmaya_bridge.skeleton import Bone

SAME: skeleton.Axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def hand(prefix: str = "", suffix: str = "", scale: float = 1.0) -> list[Bone]:
    def name(base: str) -> str:
        return f"{prefix}{base}{suffix}" if base else ""

    points = {
        "hand": ("", (0.0, 0.0, 0.0)),
        "i1": ("hand", (-3.0, -1.0, -11.6)),
        "i2": ("i1", (-3.0, -1.0, -13.7)),
        "m1": ("hand", (0.8, -0.8, -12.0)),
        "m2": ("m1", (0.8, -0.8, -14.4)),
        "t1": ("hand", (-3.8, -2.3, -5.4)),
    }
    return [
        Bone(name(key), name(parent), (at[0] * scale, at[1] * scale, at[2] * scale), SAME)
        for key, (parent, at) in points.items()
    ]


def turn_y(degrees: float) -> skeleton.Matrix3:
    angle = math.radians(degrees)
    return [
        [math.cos(angle), 0.0, math.sin(angle)],
        [0.0, 1.0, 0.0],
        [-math.sin(angle), 0.0, math.cos(angle)],
    ]


def test_clashes_and_pairs_by_name() -> None:
    names = ["|a|hand_r", "|b|hand_r", "|a|hand_l", "spine", "L_foot", "foot_l"]
    assert skeleton.clashes(names) == ["hand_r"]
    found, lonely = skeleton.pairs(["hand_r", "hand_l", "spine", "L_foot", "LeftArm"])
    assert found == [("hand_l", "hand_r")]
    assert lonely == ["L_foot", "LeftArm"]


def test_mirror_gaps_measure_twins_against_the_mirror_image() -> None:
    bones = [
        Bone("hand_r", "", (10.0, 5.0, 0.0), SAME),
        Bone("hand_l", "", (-10.0, 5.5, 0.0), SAME),
        Bone("spine", "", (0.0, 0.0, 0.0), SAME),
    ]
    gaps = skeleton.mirror_gaps(bones, 0, 0.0)
    assert gaps == [{"name": "hand_l", "twin": "hand_r", "off": 0.5}]
    assert skeleton.mirror_gaps(bones, 0, 1.0)[0]["off"] > 2.0


def test_similarity_finds_scale_turn_and_move() -> None:
    source = [bone.at for bone in hand()]
    rotation = turn_y(72.0)
    target = []
    for point in source:
        turned = skeleton.apply(rotation, point)
        target.append((2.13 * turned[0] + 26.0, 2.13 * turned[1] - 45.0, 2.13 * turned[2] - 51.0))
    fit = skeleton.similarity(source, target)
    assert fit is not None
    assert abs(fit.scale - 2.13) < 1e-9
    assert abs(skeleton.rotation_angle(fit.rotation) - 72.0) < 1e-6
    for point, want in zip(source, target, strict=True):
        assert skeleton.length(skeleton.sub(skeleton.placed(fit, point), want)) < 1e-9


def test_similarity_needs_points_off_one_line() -> None:
    line = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
    assert skeleton.similarity(line, line) is None
    assert skeleton.similarity(line[:2], line[:2]) is None


def test_compare_same_skeleton_is_same() -> None:
    result, issues = skeleton.compare(hand(), hand(), lambda name: name)
    assert issues == []
    assert result["same"] is True
    assert result["matched"] == 6
    assert result["place"]["off"] == 0
    assert "fit" not in result["place"]


def test_compare_renames_and_sees_through_scale_and_turn() -> None:
    rotation = turn_y(40.0)
    scene = []
    for bone in hand(suffix="_r", scale=2.0):
        turned = skeleton.apply(rotation, bone.at)
        at = (turned[0] + 5.0, turned[1], turned[2])
        axes = skeleton.turned_axes(skeleton.Fit(1.0, rotation, (0.0, 0.0, 0.0)), bone.axes)
        scene.append(Bone(bone.name, bone.parent, at, axes))
    result, issues = skeleton.compare(scene, hand(), lambda name: name + "_r")
    assert issues == [], issues
    fit = result["place"]["fit"]
    assert fit["scale"] == 2 and fit["turned"] == 40 and fit["off"] == 0
    assert result["lengths"]["ratio"] == 2


def test_compare_lists_what_differs() -> None:
    scene = hand()
    scene = [bone for bone in scene if bone.name != "t1"]
    scene.append(Bone("extra", "hand", (0.0, 1.0, 0.0), SAME))
    scene = [
        Bone(bone.name, "hand", bone.at, bone.axes) if bone.name == "i2" else bone for bone in scene
    ]
    tilted = skeleton.turned_axes(skeleton.Fit(1.0, turn_y(30.0), (0.0, 0.0, 0.0)), SAME)
    scene = [
        Bone(bone.name, bone.parent, (0.8, -0.8, -15.0), tilted) if bone.name == "m2" else bone
        for bone in scene
    ]
    result, issues = skeleton.compare(scene, hand(), lambda name: name)
    assert result["missing"]["items"] == ["t1"]
    assert result["extra"]["items"] == ["extra"]
    assert result["parents"]["items"] == [{"name": "i2", "scene": "hand", "file": "i1"}]
    assert result["turned"]["items"] == [{"name": "m2", "turned": 30}]
    assert result["moved"]["items"][0]["name"] == "m2"
    assert [entry["name"] for entry in result["lengths"]["odd"]["items"]] == ["i2", "m2"]
    assert len(issues) == 6 and result["same"] is False


GAME_HAND = {
    "hand": ("", (0.0, 0.0, 0.0)),
    "arm": ("hand", (0.0, 0.0, 0.0)),
    "i1": ("hand", (-2.9636, -1.102, -11.593)),
    "i2": ("i1", (-2.9636, -1.102, -13.6874)),
    "i3": ("i2", (-2.9636, -1.102, -15.1833)),
    "m1": ("hand", (0.7533, -0.7674, -11.9715)),
    "m2": ("m1", (0.7533, -0.7674, -14.4238)),
    "m3": ("m2", (0.7533, -0.7674, -16.1754)),
    "r1": ("hand", (4.4323, -0.8738, -10.9674)),
    "r2": ("r1", (4.4323, -0.8738, -12.9537)),
    "r3": ("r2", (4.4323, -0.8738, -14.3725)),
    "t1": ("hand", (-3.7807, -2.3321, -5.3565)),
    "t2": ("t1", (-6.3362, -2.4918, -7.2732)),
    "t3": ("t2", (-7.3192, -2.5532, -8.0104)),
}


def test_compare_names_the_joints_whose_offsets_were_added_twice() -> None:
    """The zombie_hand.fbx skeleton against a build that added parent positions twice."""
    reference = [Bone(name, parent, at, SAME) for name, (parent, at) in GAME_HAND.items()]
    built: dict[str, skeleton.Vec] = {}
    for name, (parent, at) in GAME_HAND.items():
        if parent and GAME_HAND[parent][0]:
            base = built[parent]
            at = (base[0] + at[0], base[1] + at[1], base[2] + at[2])
        built[name] = at
    scene = [Bone(name, parent, built[name], SAME) for name, (parent, _) in GAME_HAND.items()]
    result, issues = skeleton.compare(scene, reference, lambda name: name)
    moved = sorted(entry["name"] for entry in result["moved"]["items"])
    assert moved == ["i2", "i3", "m2", "m3", "r2", "r3", "t2", "t3"]
    assert "fit" not in result["place"]
    assert result["same"] is False and result["parents"]["total"] == 0


def test_mirror_kind_tells_behavior_from_orientation() -> None:
    axes = ((0.943, 0.236, 0.236), (-0.229, 0.972, -0.057), (-0.243, 0.0, 0.97))
    right = Bone("arm_r", "", (8.0, 1.0, 0.0), axes)
    behavior = Bone(
        "arm_l",
        "",
        (-8.0, 1.0, 0.0),
        ((0.943, -0.236, -0.236), (-0.229, -0.972, 0.057), (-0.243, 0.0, -0.97)),
    )
    assert skeleton.mirror_kind(right, behavior, 0) == "behavior"
    assert skeleton.mirror_kind(right, Bone("arm_l", "", (-8.0, 1.0, 0.0), axes), 0) == (
        "orientation"
    )
    tilted = skeleton.turned_axes(skeleton.Fit(1.0, turn_y(30.0), (0.0, 0.0, 0.0)), axes)
    assert skeleton.mirror_kind(right, Bone("arm_l", "", (0.0, 0.0, 0.0), tilted), 0) == "other"


def test_tolerance_does_not_grow_with_a_far_file() -> None:
    far = [Bone(b.name, b.parent, (b.at[0] + 500.0, b.at[1], b.at[2]), b.axes) for b in hand()]
    moved = [
        Bone(b.name, b.parent, (b.at[0], b.at[1] + 0.3, b.at[2]), b.axes) if b.name == "m2" else b
        for b in hand()
    ]
    result, _ = skeleton.compare(moved, far, lambda name: name)
    assert result["place"]["tolerance"] < 0.1
    assert [entry["name"] for entry in result["moved"]["items"]] == ["m2"]


def test_one_bad_joint_does_not_tilt_the_fit_for_the_rest() -> None:
    rotation = turn_y(40.0)
    scene = []
    for bone in hand():
        turned = skeleton.apply(rotation, bone.at)
        at = (2.0 * turned[0] + 5.0, 2.0 * turned[1], 2.0 * turned[2])
        if bone.name == "t1":
            at = (at[0] + 3.0, at[1] + 2.0, at[2])
        axes = skeleton.turned_axes(skeleton.Fit(1.0, rotation, (0.0, 0.0, 0.0)), bone.axes)
        scene.append(Bone(bone.name, bone.parent, at, axes))
    result, _ = skeleton.compare(scene, hand(), lambda name: name)
    assert result["place"]["fit"]["scale"] == 2 and result["place"]["fit"]["turned"] == 40
    assert [entry["name"] for entry in result["moved"]["items"]] == ["t1"]
    assert result["turned"]["total"] == 0


def test_a_skeleton_on_one_spot_is_not_the_same() -> None:
    stacked = [Bone(b.name, b.parent, (1.0, 1.0, 1.0), b.axes) for b in hand()]
    assert skeleton.similarity([b.at for b in hand()], [b.at for b in stacked]) is None
    result, _ = skeleton.compare(stacked, hand(), lambda name: name)
    assert result["same"] is False and "fit" not in result["place"]


def test_no_matching_names_still_says_not_the_same() -> None:
    result, issues = skeleton.compare(hand(), hand(prefix="x_"), lambda name: name)
    assert result["matched"] == 0 and result["same"] is False and issues
