# Topology

`bmaya topo` reads a mesh's edge flow: the closed edge loops around its tubes (fingers, arms, legs, a neck), the tubes they make, and where joints go in them. Agents placed hand joints this way with a numpy script after `polySelect(edgeLoop=...)` gave them open lengthwise loops; `topo` does the same in Maya, without numpy.

## The report

`bmaya topo NAME` works on one mesh. Positions, radii and gaps are in the scene's unit. `--limit` sets how many loops and tubes are listed.

| Key | Means |
|---|---|
| `verts`, `faces`, `edges` | Counts of the visible shape. |
| `shells`, `border_edges` | Separate pieces, and edges with one face (a hole or an open end). |
| `valence` | How many vertices have each number of edges: `{"4": 590, "3": 16, ...}`. Loops pass only through vertices with four edges; the others are poles where loops stop. |
| `loops` | Closed edge loops that go around a tube: `at` (centre), `radius` (mean distance of its vertices from the centre), `normal` (of the plane it lies in), `verts`, and `tube` when it belongs to one. `--ids` adds the vertex numbers, for selecting them. |
| `tubes` | Loops of a size, in parallel planes, one further along than the other and closer than their radius. Each lists its `loops` from the thick end to the thin end, `radius` at both ends, `length` along the centres, `axis` (from the first loop to the last), the `gaps` between neighbouring loops, `runs` and `joints`. |
| `joints` (count) and `tubes[].joints` | Where joints go: the centre of the middle loop of every run of three or more loops (`loops` names it), or halfway between the two middle loops of a run with an even count. |

Tubes are ordered along the shortest tree that joins their loops' centres, so a bent tube, like a curled finger or a U, keeps its loops in order. A loop is walked edge by edge: at each four-edge vertex it takes the edge that shares no face with the one it came along, the way Maya's edge loop selection goes across a quad. Only loops that come back to where they started count. Two loops join a tube when the larger radius is under 1.3 times the smaller and their centres are closer than half the sum of their radii. Tubes come thinnest first.

A run is a stretch of tightly spaced loops. Within a tube a gap larger than `--gap` times the tube's closest gap (1.2 by default) starts a new run. Modelers put three close loops around a knuckle, an elbow or a wrist, so the middle one of such a run is where the joint bends. A run of one loop marks no joint, and neither does a tube whose loops are all evenly spaced: it is one run, and nothing in it stands out.

Loops need quads: a triangulated mesh has none, and `note` says so.

## Joints between loops

`topo` places joints in the middle of runs only. A joint that is not in a run, like the first knuckle of a finger hidden in the palm, can be put on the tube's axis from the others: `fp_hands_fit.py` put it at `j2 - axis * 1.4 * |j3 - j2|` (2.6 for the thumb, which pivots deep in the palm). With the `axis` and `joints` of the report that is one line in `bmaya exec`.

## The picture

`bmaya topo NAME -o loops.png` draws the mesh from several sides like `bmaya views` (`--views`, `--size`), each tube's loops in its own color, loops outside tubes grey, joint places as black dots, and each tube labelled by its id at its thin end. Loops and dots are drawn over the mesh, so the far side shows through. `--tubes 0,3` draws only those tubes and frames the views on them, for a close look at fingers on a long arm.

## On the Zombie hand

On the author's hand mesh (`hand_r`, 623 vertices) `topo` finds the 46 loops and the 4 finger tubes plus the forearm that `fp_hands_fit.py` found, and its 9 joint places (the wrist and the second and third joint of each finger) match `fp_hands_fit.json` within 1e-4 cm. The first joints computed from `axis` with 1.4 and 2.6 match within 4e-4. One call takes 0.6 s.

The thumb's two runs are split by a gap 3.5% over the 1.2 threshold, so thresholds matter. On the same hand straightened into the game's rest pose the wrist run's gaps became 0.81 and 0.99, and no single `--gap` finds both: at 1.25 the wrist run is whole but the thumb's two runs merge into one. `--gap` applies to every tube, so when a joint is missing, read that tube's `gaps` and place it from them rather than moving the threshold for the whole mesh.
