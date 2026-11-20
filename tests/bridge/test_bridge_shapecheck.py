from bmaya_bridge.shapecheck import Problem, ShapeFacts, classify


def shape(name: str, **changes: object) -> ShapeFacts:
    base = ShapeFacts(
        name=name,
        intermediate=False,
        faces=6,
        feeds=False,
        instances=1,
        opposite=False,
        inward=False,
    )
    return base._replace(**changes)


def test_a_skinned_mesh_with_its_original_is_clean() -> None:
    assert (
        classify([shape("handShape"), shape("handShapeOrig", intermediate=True, feeds=True)]) == []
    )


def test_unused_intermediate_shapes_are_reported_not_blocking() -> None:
    strays = [shape(f"polySurfaceShape{i}", intermediate=True) for i in range(7)]
    problems = classify([shape("handShape"), *strays])
    assert problems == [
        Problem(
            "7 unused intermediate shapes, left from earlier versions: polySurfaceShape0, "
            "polySurfaceShape1, polySurfaceShape2, polySurfaceShape3, polySurfaceShape4 and 2 more",
            False,
        )
    ]


def test_empty_meshes_and_hidden_inside_out_faces_block() -> None:
    problems = classify([shape("ghostShape", faces=0)])
    assert problems == [Problem("ghostShape has no faces", True)]
    flagged = classify([shape("boxShape", opposite=True, inward=True)])
    assert len(flagged) == 1 and flagged[0].blocking
    assert "the exported file gets it inside out" in flagged[0].text
    drawn_flipped = classify([shape("boxShape", opposite=True)])
    assert len(drawn_flipped) == 1 and not drawn_flipped[0].blocking


def test_instances_and_several_visible_shapes() -> None:
    problems = classify([shape("aShape", instances=2), shape("bShape")])
    assert [p.text for p in problems] == [
        "aShape is instanced under 2 transforms",
        "2 visible shapes on one transform: aShape, bShape",
    ]
    assert not any(p.blocking for p in problems)
