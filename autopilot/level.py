"""Play a level from a short route: switches to press, things to get, the exit.

The route says only what a player needs to do in the level, in order; the
rest (walking there, doors, lifts, picking up health and ammo nearby, and
fighting) is the same for every level. Each level's script is a route plus
a call to main():

    ROUTE = [("press", 361, "the door switch"), ("get", (432, 2608), "blue key"),
             ("exit", 1367)]
    main("E1M3", ROUTE, after=["e1m1-complete.txt", "e1m2-complete.txt"])
"""

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import doom  # noqa: E402
from actions import Run  # noqa: E402
from live import Died, Live  # noqa: E402
from nav import Map  # noqa: E402

TO_NEXT_LEVEL = ["tap use", "wait 1s", "tap use", "wait 1s", "tap use", "wait 2s"]
KEY_DOORS = {"blue": (26, 32), "yellow": (27, 34), "red": (28, 33)}


def follow(r, route):
    """Do each step of the route. Returns True once the level is over."""
    L = r.L
    for step in route:
        kind, what = step[0], step[1]
        name = step[2] if len(step) > 2 else ""
        if kind == "press":
            stand, mid = r.line_stand(what)
            r.go(*stand, name=name or f"switch {what}")
            r.press_line(what, name or f"switch {what}")
            L.do("wait 1s")
        elif kind == "cross":
            # Walk over a trigger line, from its front side to its back.
            (a, _), (b, _) = r.line_stand(what, 24), r.line_stand(what, -24)
            r.go(*a, name=name or f"line {what}")
            L.goto(*b, tol=10, final=True)
        elif kind == "get":
            r.go(*what, name=name)
        elif kind == "exit":
            stand, mid = r.line_stand(what)
            r.go(*stand, name="the exit")
            r.press_line(what, "the exit switch")
            L.do("wait 1s")
        L.fight(900)
        r.m.set_keys(sum((KEY_DOORS[k] for k in L.status["keys"]), ()))
        if L.status["state"] != "level":
            return True
    return L.status["state"] != "level"


def moves_in(path):
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def main(mapname, route, after, title=None):
    """Play `route` in `mapname`, continuing from the runs in `after`."""
    level = mapname.lower()
    parser = argparse.ArgumentParser(description=f"Play {mapname} with the autopilot.")
    parser.add_argument("--out", type=Path, default=doom.ROOT / "out" / f"{level}-autopilot.txt",
                        help=f"moves file to write (default: out/{level}-autopilot.txt)")
    parser.add_argument("-q", "--quiet", action="store_true", help="don't log each step")
    args = parser.parse_args()

    wad = doom.WADS / "freedoom1.wad"
    if not doom.BINARY.exists() or not wad.exists():
        doom.build()
    before = [doom.ROOT / "examples" / f for f in after]
    prefix = [m for path in before for m in moves_in(path)] + TO_NEXT_LEVEL

    started = time.time()
    L = Live(wad, prefix, verbose=not args.quiet,
             weapon_prefs=["shotgun", "chaingun", "pistol"])
    L.kite = True
    L.dodge = True
    m = Map(wad, mapname, lifts=True, avoid_hurt=True, one_way_doors=True)
    m.set_keys(sum((KEY_DOORS[k] for k in L.status["keys"]), ()))
    r = Run(L, m)
    try:
        done = follow(r, route)
    except Died:
        L.log("died")
        done = False
    if done:
        L.do("wait 3s")   # let the stats screen count up
    status = L.status
    L.close()

    result = (f"{status['kills']} of {status['total_kills']} kills, "
              f"{status['health']}% health left" if done else "did not reach the exit")

    def shown(path):
        try:
            return path.resolve().relative_to(doom.ROOT)
        except ValueError:
            return path

    replay = " ".join(f"-f {shown(p)}" for p in before + [args.out])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        f"# All of {mapname}{f' ({title})' if title else ''}, carrying on from the level\n"
        f"# before it, to the exit. Replay with\n"
        f"# python3 doom.py play --title {replay}\n"
        "#\n"
        f"# Recorded by autopilot/{level}.py: a route of switches and keys for\n"
        "# this level, with the shared autopilot doing the walking, doors, lifts,\n"
        f"# pickups and fighting. Result: {result}.\n\n"
        + "\n".join(TO_NEXT_LEVEL) + "\n\n" + "\n".join(L.moves) + "\n")
    print(f"{'Finished' if done else 'Failed'}: {result}. {len(L.moves)} moves in "
          f"{time.time() - started:.0f} s, written to {args.out}")
    sys.exit(0 if done else 1)
