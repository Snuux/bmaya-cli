import argparse
import importlib
import sys
from collections.abc import Callable
from pathlib import Path

from bmaya_cli import __version__

LOG_TAIL = 20

DESCRIPTION = """\
bmaya drives Autodesk Maya from the command line, for AI agents and scripts.

Each command talks to a running Maya, prints one line of JSON on stdout and exits.
Errors go to stderr as "error: ..." and "hint: ..." lines. With no Maya running,
the first command starts one without a window and says so in a "note: ..." line
on stderr; that Maya stays up until `bmaya stop`.
"""

EPILOG = """\
exit codes:
  0  done
  1  the command failed; stderr says why and what to do next
  2  bad arguments
  3  not ready: Maya is still starting, or still busy with a command that was
     sent. Do not resend that command; wait and check `bmaya sessions`.

a typical loop:
  bmaya scene                               # what is in the scene
  bmaya exec "cmds.polyCube(name='crate')"  # change it with maya.cmds
  bmaya shot -o shot.png                    # look at the result
  bmaya undo                                # take your last step back
  bmaya stop                                # end a Maya you started, after saving

`bmaya COMMAND --help` explains one command. `bmaya guide` prints the manual
for agents: the working loop, a Blender-to-Maya map and recipes.
"""

Runner = Callable[[argparse.Namespace], int]


def lazy(module: str, name: str) -> Runner:
    """Commands load their module on use, so `bmaya scene` does not pay for `bmaya demo`."""

    def run(args: argparse.Namespace) -> int:
        return getattr(importlib.import_module(f"bmaya_cli.{module}"), name)(args)

    return run


def examples(*lines: str) -> str:
    return "examples:\n" + "\n".join(f"  {line}" for line in lines)


def add_frames(command: argparse.ArgumentParser, what: str) -> None:
    group = command.add_mutually_exclusive_group()
    group.add_argument(
        "--frames",
        default="",
        metavar="SPEC",
        help=f"{what}: 1,12,24 or a range 0:30 or 0:30:2 (both ends included); "
        "negative frames as --frames=-10:20",
    )
    group.add_argument(
        "--clip",
        default="",
        metavar="NAME",
        help="the frames of a Game Exporter clip or time slider bookmark (`bmaya anim` lists them)",
    )


def add_target(group: argparse._ArgumentGroup, top_level: bool) -> None:
    default = "" if top_level else argparse.SUPPRESS
    group.add_argument(
        "--session",
        default=default,
        metavar="ID",
        help="Maya session to talk to for this one command (see `bmaya sessions`)",
    )
    group.add_argument(
        "--client",
        default=default,
        metavar="LABEL",
        help="name this caller; by default it is derived from the agent that runs the command "
        "(env BMAYA_CLIENT sets it too)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bmaya",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"bmaya {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", title="commands")

    # Accepted before and after the command name. The copy on a command has no default, so
    # it does not wipe a value given before the name.
    add_target(parser.add_argument_group("which Maya, and who is asking"), top_level=True)
    maya = argparse.ArgumentParser(add_help=False)
    add_target(maya.add_argument_group("which Maya, and who is asking"), top_level=False)

    def add(
        name: str,
        runner: str,
        summary: str,
        details: str = "",
        epilog: str = "",
        module: str = "commands",
        for_maya: bool = True,
    ) -> argparse.ArgumentParser:
        command = commands.add_parser(
            name,
            help=summary,
            description=details or summary,
            epilog=epilog,
            parents=[maya] if for_maya else [],
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        command.set_defaults(run=lazy(module, runner))
        return command

    scene = add(
        "scene",
        "cmd_scene",
        "list the scene: file, units, up axis, fps, selection, nodes",
        "Prints the scene file, unit, up axis, fps, frame range, the selection and the nodes.\n"
        "Without filters the nodes are the top-level ones. `shown` and `total` say how many\n"
        "matched.",
        examples(
            "bmaya scene",
            "bmaya scene --query barrel            # names containing 'barrel'; * and ? work",
            "bmaya scene --type joint --limit 200  # every joint",
        ),
    )
    scene.add_argument("-q", "--query", default="", help="name filter, substring or wildcard")
    scene.add_argument("-t", "--type", default="", help="Maya node type: mesh, joint, camera, ...")
    scene.add_argument("-n", "--limit", type=int, default=0, help="most nodes to list (default 50)")

    obj = add(
        "object",
        "cmd_object",
        "describe one node: transform, bbox, mesh counts, materials, keys",
        "Prints the world transform, bounding box, shape, vertex and triangle counts, UV sets,\n"
        "materials, construction history and animated attributes of one node.",
        examples(
            "bmaya object crate", 'bmaya object "|props|crate"   # a full path when names clash'
        ),
    )
    obj.add_argument("name", help="node name or full DAG path")

    execute = add(
        "exec",
        "cmd_exec",
        "run Python inside Maya (cmds, mel and om are preloaded)",
        "Runs Python on Maya's main thread as one undo step. `cmds`, `mel` and `om`\n"
        "(OpenMaya 2) are already imported. Assign to a variable named `result` to get a value\n"
        "back; what the code prints comes back as `stdout`.\n\n"
        "The code comes from the argument, from -f FILE, or from stdin. Use stdin or a file for\n"
        "anything with quotes or several lines, so the shell does not mangle it.",
        examples(
            'bmaya exec "result = cmds.ls(selection=True)"',
            "bmaya exec -f build_barrel.py",
            "bmaya exec <<'PY'                         # bash, zsh",
            'cube = cmds.polyCube(name="crate")[0]',
            "result = cmds.ls(cube, long=True)",
            "PY",
            "Get-Content build.py -Raw | bmaya exec     # PowerShell",
        ),
    )
    execute.add_argument(
        "code", nargs="?", default="", help="Python source; '-' or nothing reads stdin"
    )
    execute.add_argument("-f", "--file", default="", help="read the code from this file")

    shot = add(
        "shot",
        "cmd_shot",
        "save a viewport screenshot (or a contact sheet of frames) as PNG",
        "Writes a PNG of the viewport and prints its path, width and height. Open that file to\n"
        "look at the scene. With --frames the frames are laid out in one contact sheet, which\n"
        "is the cheap way to review an animation. A range takes both ends (0:30 is 31\n"
        "frames) and is thinned evenly to the 12 tiles of a sheet.\n\n"
        "A Maya without a window renders offscreen from `persp` or the camera you name, in\n"
        "the shape of the render resolution; frame the subject first with\n"
        "cmds.viewFit('persp', all=True). --light changes the lighting for this shot only:\n"
        "unlit shows texture and base colors as they are, soft adds an even studio light,\n"
        "scene uses the scene's lights.\n\n"
        "--trail draws the world path of up to 4 nodes over the frames (or the playback\n"
        "range for one frame): a dot per frame, so spacing shows, a bigger dot with its\n"
        "number on each key, and a ring where the node is on each tile.\n\n"
        "--fit frames the named nodes on every frame of the shot with a temporary copy of\n"
        "the camera, which keeps its direction; the camera itself is not touched.",
        examples(
            "bmaya shot -o shot.png",
            "bmaya shot --camera front --size 1200 -o front.png",
            "bmaya shot --camera front --aspect 1:1 --light unlit -o front.png",
            "bmaya shot --frames 1,6,12,18,24 -o walk.png",
            "bmaya shot --frames 0:32 -o walk.png        # 12 evenly spaced frames of 0..32",
            "bmaya shot --clip zombie_walk -o walk.png   # a clip from `bmaya anim`",
            "bmaya shot --clip zombie_walk --trail hand_r,foot_l -o arcs.png",
            "bmaya shot --clip zombie_walk --camera side --fit zombie -o walk_side.png",
        ),
    )
    shot.add_argument("-o", "--output", default="", metavar="FILE", help="where to write the PNG")
    shot.add_argument("--camera", default="", help="camera to look through (default: the view)")
    add_frames(shot, "frames for a contact sheet")
    shot.add_argument("--size", type=int, default=0, help="longest side in pixels (default 800)")
    shot.add_argument(
        "--aspect",
        default="",
        metavar="W:H",
        help="frame shape like 1:1 or 16:9, or 'render' for the render resolution "
        "(default: the viewport, or the render resolution without a window)",
    )
    shot.add_argument(
        "--fit",
        default="",
        metavar="A,B",
        help="frame these nodes on every frame, looking the camera's way",
    )
    shot.add_argument(
        "--trail",
        default="",
        metavar="A,B",
        help="draw the paths of these nodes (up to 4) over the frames",
    )
    shot.add_argument(
        "--light",
        default="",
        choices=("unlit", "soft", "scene"),
        help="lighting for this shot (default: as the viewport has it)",
    )

    views = add(
        "views",
        "cmd_views",
        "draw a model from several sides in one PNG: parts, edges, sizes",
        "Writes one PNG with the model seen from several sides, each tile labelled, and\n"
        "prints its path. This is the way to check modeling work: proportions, how parts\n"
        "meet, topology. With no names it draws the selection, or every visible mesh.\n\n"
        "Views: front, back, left, right, top, bottom, persp and persp-back (three-quarter\n"
        "from the front and from behind), or all. The default is front,right,top,persp.\n"
        "Straight-on views carry the width and height of what they show, in scene units.\n\n"
        "Styles:\n"
        "  parts       a color per mesh (`parts` in the result names them), edges on top.\n"
        "              A face seen from behind is dark: reversed normals show at once\n"
        "  silhouette  the outline alone, to judge the shape\n"
        "  xray        pale surfaces with every edge, hidden ones too: what is inside\n"
        "  shaded      Maya's own render with materials and textures; takes --light. It\n"
        "              shows the whole scene, framed on the named nodes\n"
        "The first three are drawn by bmaya itself, so they look the same with or without\n"
        "a Maya window and change nothing in the scene. Past about 10000 triangles per\n"
        "400 px tile the edges are left out and `note` says so.\n\n"
        "--section cuts the model with a plane across a world axis and adds a tile that\n"
        "looks at the cut: wall thickness, parts hidden inside, what pokes through.",
        examples(
            "bmaya views crate -o crate.png",
            "bmaya views hand --views all -o hand.png",
            "bmaya views hand --views front,persp --style silhouette -o shape.png",
            "bmaya views engine --style xray --section x -o inside.png",
            "bmaya views engine --section y=12.5 -o cut.png",
            "bmaya views hero --style shaded --light soft -o look.png",
        ),
    )
    views.add_argument("names", nargs="*", help="mesh or group names")
    views.add_argument("-o", "--output", default="", metavar="FILE", help="where to write the PNG")
    views.add_argument(
        "--views", default="", metavar="A,B", help="views to draw, or all (default 4 views)"
    )
    views.add_argument(
        "--style",
        default="",
        choices=("parts", "silhouette", "xray", "shaded"),
        help="how to draw (default parts)",
    )
    views.add_argument("--no-wire", action="store_true", help="leave the edges out")
    views.add_argument(
        "--section",
        default="",
        metavar="AXIS[=POS]",
        help="add a cut across x, y or z, at POS or through the middle",
    )
    views.add_argument(
        "--light",
        default="",
        choices=("unlit", "soft", "scene"),
        help="lighting for --style shaded",
    )
    views.add_argument(
        "--size", type=int, default=0, help="longest side of the sheet in pixels (default 1600)"
    )

    playblast = add(
        "playblast",
        "cmd_playblast",
        "render frames into a GIF, an MP4 or a folder of PNGs",
        "Renders every frame of a range or clip and joins them with ffmpeg when it is on\n"
        "PATH: a GIF with its own palette, or an H.264 MP4, at the scene's frame rate (divided\n"
        "by the step). Without ffmpeg the PNG frames stay in a folder next to OUT; a folder\n"
        "name as OUT always gives PNGs. Frames go to Maya in chunks small enough to answer in\n"
        "time, so a long range never times out. A frame takes about 0.2-0.4 s, so run long\n"
        "ranges in the background. --camera, --size, --aspect, --light, --trail and --fit\n"
        "work as in `bmaya shot`.\n\n"
        "A preview is for people. To judge motion yourself use `bmaya motion` and\n"
        "`bmaya shot --frames`, which you can read.",
        examples(
            "bmaya playblast walk.gif --clip zombie_walk --camera side --fit zombie",
            "bmaya playblast out/attack.mp4 --frames 3380:3416 --light soft --size 960",
            "bmaya playblast frames/walk --clip zombie_walk   # PNG frames only",
        ),
        module="playblast",
    )
    playblast.add_argument("output", metavar="OUT", help="walk.gif, walk.mp4, or a folder")
    add_frames(playblast, "frames to render (default: the playback range)")
    playblast.add_argument("--camera", default="", help="camera to look through")
    playblast.add_argument(
        "--size", type=int, default=0, help="longest side in pixels (default 640)"
    )
    playblast.add_argument("--aspect", default="", metavar="W:H", help="frame shape like 16:9")
    playblast.add_argument(
        "--light", default="", choices=("unlit", "soft", "scene"), help="lighting for the frames"
    )
    playblast.add_argument("--trail", default="", metavar="A,B", help="draw these nodes' paths")
    playblast.add_argument(
        "--fit", default="", metavar="A,B", help="keep these nodes in view on every frame"
    )

    audit = add(
        "audit",
        "cmd_audit",
        "check meshes for game export: n-gons, history, transforms, UVs",
        "Reports per mesh the vertex and triangle counts, faces by kind, the size in scene\n"
        "units, and the issues that matter for a game asset: n-gons, non-manifold geometry,\n"
        "faces wound against their neighbours, normals pointing inward, unfrozen transforms,\n"
        "construction history, missing or overlapping UVs. Locked normals, UVs outside 0-1\n"
        "and mirrored UV faces are counted but are not issues: assets have them on purpose.\n"
        "With no names it audits the selection, or every mesh when nothing is selected.\n\n"
        "--symmetry checks that every vertex has a twin mirrored across the plane through\n"
        "the mesh's pivot, and lists a few vertices that have none.\n\n"
        "`bmaya cleanup NAME` fixes most of the geometry issues listed here.",
        examples("bmaya audit", "bmaya audit barrel_geo crate", "bmaya audit hero --symmetry x"),
    )
    audit.add_argument("names", nargs="*", help="mesh or group names")
    audit.add_argument(
        "--symmetry",
        default="",
        choices=("x", "y", "z"),
        help="check mirror symmetry across this world axis",
    )

    diff = add(
        "diff",
        "cmd_diff",
        "show what an edit changed in a mesh: counts, size, moved vertices",
        "Compares a mesh with how it was at `bmaya diff NAME --mark`: vertex, triangle and\n"
        "face counts, size, surface area and volume before and after. When the topology is\n"
        "the same it also says how many vertices moved, how far, and the box they are in,\n"
        "so an edit that touched more than it should shows up. `remesh`, `reduce` and\n"
        "`cleanup` mark the mesh themselves before they run.\n\n"
        "A mark is yours alone and lives in that Maya until it stops. --against compares\n"
        "two meshes as they are now, like a copy made before the edit.",
        examples(
            "bmaya diff crate --mark        # before the edit",
            "bmaya diff crate               # after it",
            "bmaya diff crate --against crate_backup",
        ),
    )
    diff.add_argument("name", help="the mesh")
    diff.add_argument("--mark", action="store_true", help="remember the mesh as it is now")
    diff.add_argument(
        "--against", default="", metavar="OTHER", help="compare with this mesh instead of the mark"
    )

    remesh = add(
        "remesh",
        "cmd_remesh",
        "rebuild a mesh's topology: even triangles, or clean quads",
        "Replaces the topology of one mesh and keeps its shape. Use it instead of moving\n"
        "and merging vertices by hand.\n"
        "  --to tris   even triangles with edges near --edge (default: an edge that keeps\n"
        "              about the triangle count). Good before sculpting, booleans or a\n"
        "              retopology. At most 500000 triangles come out\n"
        "  --to quads  Maya's Retopologize: a quad mesh of about --faces faces (default:\n"
        "              half the triangle count). It can take a minute on a dense mesh\n"
        "UVs, vertex colors and skin weights do not survive: remesh first, unwrap and\n"
        "skin after. The result has `before`, `after` and the audit `issues` of the new\n"
        "mesh. It is one undo step.\n\n"
        "If the command exits with code 3, Maya is still working on it: do not send it\n"
        "again. Wait, then `bmaya diff NAME` shows the mesh before and after.",
        examples(
            "bmaya remesh rock --to tris --edge 0.5",
            "bmaya remesh rock --to quads --faces 800",
            "bmaya views rock -o rock.png     # look at the result",
        ),
    )
    remesh.add_argument("name", help="the mesh")
    remesh.add_argument(
        "--to", default="", choices=("tris", "quads"), help="what to build (default tris)"
    )
    remesh.add_argument(
        "--edge", type=float, default=0, metavar="LENGTH", help="edge length for --to tris"
    )
    remesh.add_argument(
        "--faces", type=int, default=0, metavar="N", help="face count for --to quads"
    )
    remesh.add_argument(
        "--keep-hard-edges", action="store_true", help="--to quads: keep hard edges in place"
    )

    reduce = add(
        "reduce",
        "cmd_reduce",
        "lower a mesh's triangle count and keep its shape",
        "Reduces one mesh to a triangle budget (--tris) or to a share of its triangles\n"
        "(--percent 50 keeps half). UVs and the silhouette are kept as well as the budget\n"
        "allows; the --keep flags pin borders, UV seams, hard edges or quads. The result\n"
        "has `before`, `after` and the audit `issues` of the new mesh. It is one undo step.",
        examples(
            "bmaya reduce statue --tris 5000",
            "bmaya reduce statue --percent 25 --keep-uvs --keep-borders",
        ),
    )
    reduce.add_argument("name", help="the mesh")
    reduce.add_argument("--tris", type=int, default=0, metavar="N", help="triangles to end with")
    reduce.add_argument(
        "--percent", type=float, default=0, metavar="P", help="share of triangles to keep"
    )
    reduce.add_argument("--keep-borders", action="store_true", help="do not move open borders")
    reduce.add_argument("--keep-uvs", action="store_true", help="do not move UV seams")
    reduce.add_argument("--keep-hard-edges", action="store_true", help="do not move hard edges")
    reduce.add_argument("--keep-quads", action="store_true", help="keep quads where possible")

    cleanup = add(
        "cleanup",
        "cmd_cleanup",
        "fix a mesh's geometry: merge vertices, drop bad faces, conform normals",
        "Fixes what `bmaya audit` complains about in one mesh: merges vertices that sit on\n"
        "the same spot, removes zero-area faces, zero-length edges and lamina faces, splits\n"
        "non-manifold geometry, makes all faces wind the same way and turns the normals\n"
        "outward. UV seams are kept. The result has `before`, `after` and the audit\n"
        "`issues` that are left. It is one undo step.",
        examples(
            "bmaya cleanup crate",
            "bmaya cleanup scan --merge 0.01 --ngons   # also triangulate n-gons",
        ),
    )
    cleanup.add_argument("name", help="the mesh")
    cleanup.add_argument(
        "--merge",
        type=float,
        default=0,
        metavar="DIST",
        help="merge vertices closer than this (default: 0.001%% of the mesh's size)",
    )
    cleanup.add_argument("--ngons", action="store_true", help="triangulate faces with 5+ sides")

    anim = add(
        "anim",
        "cmd_anim",
        "report animation: curves, key ranges, fps, skeleton, skin",
        "Without a name it summarises the animation in the scene: clips (Game Exporter clips\n"
        "and time slider bookmarks), animated nodes, skeletons, skin clusters, blend shapes\n"
        "and layers. With a name it lists that node's curves, key ranges and tangents, and\n"
        "its skeleton and skin clusters.\n\n"
        "--plot draws the node's curves as a PNG, like the Graph Editor: one panel each for\n"
        "translate, rotate, scale and the rest, dots on keys, frame numbers, clips shaded,\n"
        "red ticks where a rotate curve jumps more than 180 between keys. Eases, overshoot,\n"
        "holds and a cycle that does not close are easy to see there.",
        examples(
            "bmaya anim",
            "bmaya anim root",
            "bmaya anim hand_r_ctrl --plot -o curves.png",
            "bmaya anim hips --plot --attrs translateY,rx --clip zombie_walk -o hips.png",
        ),
    )
    anim.add_argument("name", nargs="?", default="", help="node to describe")
    anim.add_argument("--plot", action="store_true", help="draw the node's curves as a PNG")
    anim.add_argument("-o", "--output", default="", metavar="FILE", help="where to write the PNG")
    anim.add_argument(
        "--attrs", default="", metavar="A,B", help="plot only these attributes (rx, translate, ik)"
    )
    add_frames(anim, "frames to plot (default: the keys' range)")
    anim.add_argument("--size", type=int, default=0, help="width in pixels (default 1000)")

    rig = add(
        "rig",
        "cmd_rig",
        "list a rig's controls: keyable attributes, limits, IK, constraints, switches",
        "Lists what an animator would key: transforms with curve shapes and anything that\n"
        "already has keys, or the joints when there are no controls. For each control: its\n"
        "keyable attributes, `driven` ones (fed by a constraint, a connection or an IK\n"
        "handle, so keys on them do nothing), values that differ from the default, limits,\n"
        "rotate order and keys. Then IK handles with their pole and driver, constraints,\n"
        "switch attributes (like ik that drive ikBlend, visibility or constraint weights)\n"
        "and character sets.\n"
        "Without a root it looks at the whole scene.",
        examples("bmaya rig", "bmaya rig rig_ctrls", 'bmaya rig "|hunter"'),
    )
    rig.add_argument("root", nargs="?", default="", help="top node of the rig")

    skin = add(
        "skin",
        "cmd_skin",
        "report skin weights, save and load them, copy, mirror, prune",
        "Without an edit flag it reports the skin of each mesh: the skin cluster, its\n"
        "influences with how many vertices each moves, the most influences on one vertex,\n"
        "influences with no weight (`unused`), whether the skeleton stands at the bind pose,\n"
        "and the issues a game engine trips on: vertices over --max influences (default 4),\n"
        "weights that do not add up to 1, unweighted vertices, dual quaternion skinning,\n"
        "influences that are not joints. `sample` lists a few vertex numbers per issue;\n"
        "--verts 0:20 prints those vertices' weights. With no names it reports the\n"
        "selection, or every skinned mesh.\n\n"
        "Weights go to and from a JSON file of one {joint: weight} object per vertex:\n"
        "  --save FILE       write the mesh's weights\n"
        '  --load FILE       set them from such a list, or from a file whose "weights"\n'
        '                    holds it; {"12": {...}} sets only the vertices it names\n'
        "  --copy-from SRC   every vertex takes the weights of the closest point on SRC's\n"
        "                    surface at its bind pose: for a remeshed or edited copy\n"
        "  --mirror AXIS     with --copy-from: take them from the mirror image of SRC (the\n"
        "                    left arm from the right one). Alone: copy one half of the mesh\n"
        "                    onto the other, +x onto -x with --mirror +x. Joints swap sides\n"
        "                    by name (hand_r/hand_l, L_hand, LeftHand). The plane goes\n"
        "                    through 0; +x=12.5 moves it\n"
        "  --rename MAP      joint names of the file or SRC: {}_r adds a suffix, rig:{} a\n"
        "                    namespace, arm=forearm_r maps one name\n"
        "  --prune [MIN]     drop weights under MIN (default 0.01)\n"
        "  --normalize       make every vertex's weights add up to 1\n"
        "  --max N           with an edit: keep the N biggest weights per vertex\n"
        "A mesh with no skin is bound to the joints the weights name. Changed vertices are\n"
        "normalized. Each edit is one undo step; the result has `changed`, the counts that\n"
        "were pruned, capped or normalized, and the issues left.",
        examples(
            "bmaya skin",
            "bmaya skin hand_r --verts 0:10",
            "bmaya skin hand_r --save hand_r_weights.json",
            "bmaya skin hand_r --load fit.json --rename {}_r",
            "bmaya skin hand_l --copy-from hand_r --mirror x",
            "bmaya skin body_new --copy-from body --prune --max 4",
            "bmaya skin body --mirror +x",
        ),
    )
    skin.add_argument("names", nargs="*", help="meshes; an edit takes one")
    skin.add_argument(
        "--max", type=int, default=0, metavar="N", help="influences allowed per vertex"
    )
    skin.add_argument("--verts", default="", metavar="SPEC", help="print these vertices' weights")
    skin.add_argument(
        "--limit", type=int, default=0, metavar="N", help="influences listed per mesh (default 30)"
    )
    source = skin.add_mutually_exclusive_group()
    source.add_argument("--save", default="", metavar="FILE", help="write the weights to a file")
    source.add_argument("--load", default="", metavar="FILE", help="set the weights from a file")
    source.add_argument(
        "--copy-from", default="", metavar="SRC", help="take the weights from a skinned mesh"
    )
    skin.add_argument(
        "--mirror", default="", metavar="AXIS", help="mirror across x, y or z (+x, -x=2.5)"
    )
    skin.add_argument("--rename", default="", metavar="MAP", help="map joint names: {}_r, a=b")
    skin.add_argument(
        "--prune",
        nargs="?",
        const="0.01",
        default="",
        metavar="MIN",
        help="drop weights below MIN (default 0.01)",
    )
    skin.add_argument("--normalize", action="store_true", help="make weights add up to 1")

    motion = add(
        "motion",
        "cmd_motion",
        "check motion: pops, flips, foot slide, loop seam, key problems",
        "Samples the world position and rotation of nodes on every frame, the way playback\n"
        "shows them (IK, constraints, expressions), and reports per node the path length,\n"
        "height range and peak speed, plus problems with their measurements:\n"
        "  pop, flip       a jump in one frame, far above the motion around it\n"
        "  loop_pose/speed with --loop: the last frame does not repeat the first\n"
        "  foot_slide      with --contacts: a planted node moves along the ground\n"
        "  below_ground    with --contacts: a node under --ground (default 0)\n"
        "  euler_jump      a rotate curve jumps more than 180 between keys\n"
        "  clip_edge       a channel has no key on a clip's first or last frame\n"
        "  subframe_keys, stray_keys  keys between frames, or outside every clip\n"
        "Thresholds scale with the size of what is checked; `size` and `limits` say what\n"
        "was used. Distances are scene units, speeds scene units per second, turns degrees.\n"
        "Name what you check: a group stands for everything under it, a skeleton's top\n"
        "joint for the whole skeleton. Without names it checks the selection, else every\n"
        "joint and animated object, which mixes in other characters.\n\n"
        "--values adds the world position of each node per frame; --attrs lists those\n"
        "attributes instead. Both keep to 3000 numbers by taking every Nth frame.",
        examples(
            "bmaya motion root --clip zombie_walk --loop --contacts foot_l,foot_r",
            "bmaya motion hand_r spine_02 --frames 0:48",
            'bmaya motion "*_ctrl" --frames 20000:20960:2',
            "bmaya motion hand_r --clip zombie_walk --values",
            "bmaya motion hand_r_ik_ctrl --frames 0:30 --attrs translate,ik",
        ),
    )
    motion.add_argument("names", nargs="*", help="nodes to check; * and ? work")
    add_frames(motion, "frames to sample (default: the playback range)")
    motion.add_argument(
        "--loop", action="store_true", help="the frames are a cycle: compare the first and last"
    )
    motion.add_argument(
        "--contacts",
        default="",
        metavar="A,B",
        help="nodes that touch the ground, like feet: check sliding and sinking",
    )
    motion.add_argument(
        "--ground", default="", metavar="H", help="ground height in scene units (default 0)"
    )
    motion.add_argument(
        "--values", action="store_true", help="add the world position of each node per frame"
    )
    motion.add_argument(
        "--attrs", default="", metavar="A,B", help="list these attributes per frame instead"
    )

    export = add(
        "export",
        "cmd_export",
        "export FBX with a Unity, Unreal or Godot preset, then check the written file",
        "Writes an FBX file. Units are not converted: the scene unit is what the importer\n"
        "sees. The unreal preset triangulates and writes smoothing groups. --animation bakes\n"
        "the playback range, or --start/--end, or the frames of --clip (which implies\n"
        "--animation), into one take named after the clip or the file.\n"
        "Before writing, the meshes are checked: a mesh with no faces, or one whose opposite\n"
        "flag hides inside-out faces, stops the export (--ignore-checks writes it anyway).\n"
        "After writing, the file is read back: `written` says what it holds, and `issues`\n"
        "lists what is missing or wrong in it, like a mesh that came out empty, joints that\n"
        "are not there, a take with the wrong frames or keys baked at another frame rate.\n"
        "--game-exporter exports the way the scene's Game Exporter nodes are set up, like\n"
        "their Export button: their paths, sets and presets, a file per clip with one take\n"
        "each. Without a NODE it runs every node in the scene; --clip NAME runs one clip.\n"
        "--copy-textures copies the model's textures next to its FBX and points the file at\n"
        "the copies, for engines that look for them there.",
        examples(
            "bmaya export hero.fbx",
            "bmaya export out/walk.fbx --preset unreal --selection --animation --start 0 --end 24",
            "bmaya export out/walk.fbx --preset godot --selection --clip zombie_walk",
            "bmaya export --game-exporter                     # every Game Exporter node",
            "bmaya export --game-exporter gameExporter_fp --clip fp_eat",
            "bmaya export --game-exporter gameExporter_model --copy-textures",
        ),
        module="exporting",
    )
    export.add_argument("path", metavar="FILE.fbx", nargs="?", default="", help="target file")
    export.add_argument("--preset", default="", help="unity (default), unreal or godot")
    export.add_argument("--selection", action="store_true", help="export only the selection")
    export.add_argument("--animation", action="store_true", help="bake and export animation")
    export.add_argument("--start", default="", help="first frame (default: playback range)")
    export.add_argument("--end", default="", help="last frame (default: playback range)")
    export.add_argument(
        "--clip",
        default="",
        metavar="NAME",
        help="bake the frames of this clip (`bmaya anim` lists them)",
    )
    export.add_argument(
        "--ignore-checks",
        action="store_true",
        help="write the file even when the checks before export find a mesh that would come out "
        "broken",
    )
    export.add_argument(
        "--game-exporter",
        nargs="?",
        const="*",
        default="",
        metavar="NODE",
        help="export as the Game Exporter nodes are set up (one node, or all of them)",
    )
    export.add_argument(
        "--copy-textures",
        action="store_true",
        help="with --game-exporter: copy model textures next to the FBX and point the file at them",
    )

    inspect = add(
        "inspect",
        "cmd_inspect",
        "describe an FBX, GLB or glTF file without Maya: meshes, skeleton, takes, textures",
        "Reads a model file directly; no Maya is started or needed. Prints the unit, up axis\n"
        "and fps the file declares, every mesh with its vertex, face and triangle counts, UV\n"
        "sets, materials and skin, the skeleton with world positions at bind time (`from`\n"
        "says where they came from), the takes with their frame ranges and curve counts, and\n"
        "the textures with whether each file is found. `issues` lists empty meshes,\n"
        "unweighted vertices, joint names used twice and missing textures.\n"
        "Reads binary .fbx (7.1 and newer), .glb and .gltf. Positions are in the file's unit.",
        examples(
            "bmaya inspect hero.fbx",
            "bmaya inspect game/assets/hands.glb --axes   # joint axes too",
            "bmaya inspect walk.fbx --limit 200",
        ),
        module="modelfile",
        for_maya=False,
    )
    inspect.add_argument("path", metavar="FILE", help=".fbx, .glb or .gltf file")
    inspect.add_argument(
        "--axes", action="store_true", help="add the x, y and z axis of every joint"
    )
    inspect.add_argument(
        "--limit", type=int, default=0, help="longest listing (default 50, at most 1000)"
    )

    file = add(
        "file",
        "cmd_file",
        "new, open, save or import a scene file",
        "new and open replace the scene. They are refused when the scene has unsaved changes,\n"
        "and when another client used this Maya in the last 10 minutes. --force overrides\n"
        "both; use it only when the user asked for it.\n"
        "save without a path saves the current file; with a path it saves as .ma or .mb.\n"
        "import adds a .ma, .mb, .fbx or .obj file to the scene.",
        examples(
            "bmaya file save",
            "bmaya file save scenes/barrel.ma",
            "bmaya file open scenes/barrel.ma",
            "bmaya file import props/crate.fbx",
            "bmaya file new",
        ),
    )
    file.add_argument("action", choices=["new", "open", "save", "import"])
    file.add_argument("path", nargs="?", default="", help="scene or model file")
    file.add_argument("--force", action="store_true", help="discard unsaved changes")

    undo = add(
        "undo",
        "cmd_undo",
        "undo your own last steps, never someone else's",
        "Undoes your last steps, one per `bmaya exec`, `remesh`, `reduce` or `cleanup`\n"
        "call. It stops at a change made by\n"
        "another client or by the user in the Maya window: the result then carries\n"
        "`blocked_by`, and with nothing undone it is an error. Ask the user before going\n"
        "on. --force also undoes changes the user made in the Maya window on top of your\n"
        "step; it never touches another client's steps.",
        examples("bmaya undo", "bmaya undo 3"),
    )
    undo.add_argument("steps", nargs="?", type=int, default=1, help="how many steps (default 1)")
    undo.add_argument("--force", action="store_true", help="also undo the user's UI changes")

    add(
        "sessions",
        "cmd_sessions",
        "list running Mayas: id, scene, busy, clients",
        "Lists every running Maya with the bridge. `clients` counts the callers that ran a\n"
        "command there in the last 10 minutes: more than one means you share that Maya.\n"
        "`busy` is true while a command runs. `client` is the name you act under.",
        examples("bmaya sessions"),
    )

    use = add(
        "use",
        "cmd_use",
        "pick the Maya that your next commands talk to",
        "Needed only when several Mayas run. The choice is remembered for this caller.",
        examples("bmaya use 12345"),
    )
    use.add_argument("id", help="session id from `bmaya sessions`")

    start = add(
        "start",
        "cmd_start",
        "start a Maya (without a window unless --gui) and wait for it",
        "Starts one more Maya, waits until it answers and makes it your current session. It\n"
        "keeps running after this command returns, with no timer: stop it with `bmaya stop`.\n"
        "You rarely need this: with no Maya running, any command starts one by itself.",
        examples("bmaya start", "bmaya start --scene scenes/barrel.ma", "bmaya start --gui"),
    )
    start.add_argument("--gui", action="store_true", help="start Maya with its window")
    start.add_argument("--scene", default="", metavar="FILE", help="scene file to open")

    stop = add(
        "stop",
        "cmd_stop",
        "stop a Maya that runs without a window",
        "Ends a Maya that runs without a window. It is refused when the scene has unsaved\n"
        "changes or another client used that Maya in the last 10 minutes; save first, or pass\n"
        "--force. A Maya with a window is the user's and is never stopped from here.",
        examples("bmaya file save scenes/barrel.ma", "bmaya stop", "bmaya stop 12345 --force"),
    )
    stop.add_argument("id", nargs="?", default="", help="session id (default: your current Maya)")
    stop.add_argument("--force", action="store_true", help="discard unsaved changes")

    add(
        "guide",
        "cmd_guide",
        "print the manual for agents (plain text, not JSON)",
        for_maya=False,
    )

    install = add(
        "install",
        "cmd_install",
        "install the Maya module, so a Maya you open has the bridge",
        module="admin",
        for_maya=False,
    )
    install.add_argument("--maya-app-dir", type=Path, help="Maya user folder (MAYA_APP_DIR)")

    uninstall = add(
        "uninstall", "cmd_uninstall", "remove the Maya module", module="admin", for_maya=False
    )
    uninstall.add_argument("--maya-app-dir", type=Path, help="Maya user folder (MAYA_APP_DIR)")

    doctor = add(
        "doctor",
        "cmd_doctor",
        "check the install and the running Mayas (plain text)",
        module="admin",
        for_maya=False,
    )
    doctor.add_argument("--maya-app-dir", type=Path, help="Maya user folder (MAYA_APP_DIR)")

    logs = add(
        "logs",
        "cmd_logs",
        "show what was sent to a Maya, and what it crashed on (plain text)",
        module="admin",
        for_maya=False,
    )
    logs.add_argument("--session", type=int, help="pid of the Maya session (default: newest)")
    logs.add_argument("--tail", type=int, default=LOG_TAIL, help="records to show (default 20)")
    logs.add_argument("--crashes", action="store_true", help="list crashed sessions instead")

    demo = add(
        "demo",
        "cmd_demo",
        "build, check and export a prop in a running Maya (plain text)",
        module="demo",
        for_maya=False,
    )
    demo.add_argument("--out", type=Path, default=Path("demo"), help="output folder")
    demo.add_argument("--session", default="", help="session id when several Mayas run")
    return parser


def use_utf8() -> None:
    """The Windows code page would otherwise mangle node names and piped code."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def main(argv: list[str] | None = None) -> int:
    use_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return args.run(args)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        from bmaya_cli.commands import report_failure

        code = report_failure(error)
        if code is None:
            raise
        return code
