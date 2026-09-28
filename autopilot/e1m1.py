#!/usr/bin/env python3
"""Play Freedoom's E1M1 from the title screen to the exit switch.

    python3 autopilot/e1m1.py                                  # writes out/e1m1-autopilot.txt
    python3 autopilot/e1m1.py --out examples/e1m1-complete.txt

The route comes from A* over the map (nav.py), re-planned after every leg.
Doors on the way get opened with "use", and any monster in sight within 900
units gets shot before moving on (live.py). The run is written out as a moves
file that `doom.py play --title -f FILE` replays exactly.

Only tested on E1M1, which needs no keycards. It doesn't pick up keys, ride
lifts, or go looking for health.
"""

import argparse
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import doom  # noqa: E402
from live import Died, Live  # noqa: E402
from nav import DOOR_SPECIALS, Map, segs_cross  # noqa: E402

# Title screen -> New Game -> Episode 1 -> the default skill.
MENU = ["wait 1.5s", "tap esc", "wait 0.6s", "tap enter", "wait 0.6s",
        "tap enter", "wait 0.6s", "tap enter", "wait 1.5s"]
GOAL = (-360, 1296)     # in front of the exit switch
SWITCH = (-420, 1296)   # the switch itself, on the west wall


def first_door_crossing(door_lines, p, q):
    """The first door line the leg p -> q crosses: (fraction along, line, point)."""
    best = None
    for i, a, b in door_lines:
        if segs_cross(p[0], p[1], q[0], q[1], a[0], a[1], b[0], b[1]):
            x1, y1 = p
            x2, y2 = q
            x3, y3 = a
            x4, y4 = b
            den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
            t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
            pt = (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
            if best is None or t < best[0]:
                best = (t, i, pt)
    return best


def run(L, m, max_legs=60):
    door_lines = [(i, m.V[l[0]], m.V[l[1]]) for i, l in enumerate(m.lines)
                  if l[3] in DOOR_SPECIALS]
    opened = set()
    for leg in range(max_legs):
        s = L.status
        if s["state"] != "level":
            return True
        here = (s["x"], s["y"])
        if math.hypot(GOAL[0] - here[0], GOAL[1] - here[1]) < 30:
            break
        cells = m.path(here, GOAL)
        if not cells:
            L.log("no path!")
            return False
        wps = m.waypoints(cells)
        ahead = [w for w in wps[1:] if math.hypot(w[0] - here[0], w[1] - here[1]) > 45]
        nxt = ahead[0] if ahead else GOAL
        final = len(ahead) <= 1

        cross = first_door_crossing(door_lines, here, nxt)
        if cross and cross[1] not in opened:
            t, li, pt = cross
            d = math.hypot(pt[0] - here[0], pt[1] - here[1])
            ux, uy = (pt[0] - here[0]) / d, (pt[1] - here[1]) / d
            L.log(f"door (line {li}) ahead at ({pt[0]:.0f},{pt[1]:.0f})")
            if d > 70:
                L.goto(pt[0] - 56 * ux, pt[1] - 56 * uy, tol=16, final=True)
            L.settle()
            L.use_toward(*pt)
            # Both faces of a door are door lines; count the door as opened.
            for j, a, b in door_lines:
                if abs((a[0] + b[0]) / 2 - pt[0]) < 40 and abs((a[1] + b[1]) / 2 - pt[1]) < 40:
                    opened.add(j)
            opened.add(li)
            continue

        L.log(f"leg {leg}: -> ({nxt[0]:.0f},{nxt[1]:.0f})")
        if not L.goto(nxt[0], nxt[1], tol=24 if final else 40, final=final):
            return False
    else:
        L.log("ran out of legs")
        return False

    L.goto(*GOAL, tol=12, final=True)
    L.settle()
    L.log("at the exit switch")
    L.use_toward(*SWITCH)
    L.do("wait 1s")
    L.log("state now", L.status["state"])
    return L.status["state"] != "level"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=doom.ROOT / "out" / "e1m1-autopilot.txt",
                        help="moves file to write (default: out/e1m1-autopilot.txt)")
    parser.add_argument("-q", "--quiet", action="store_true", help="don't log each step")
    args = parser.parse_args()

    wad = doom.WADS / "freedoom1.wad"
    if not doom.BINARY.exists() or not wad.exists():
        doom.build()

    started = time.time()
    m = Map(wad, "E1M1")
    L = Live(wad, MENU, verbose=not args.quiet)
    try:
        done = run(L, m)
    except Died:
        L.log("died")
        done = False
    if done:
        L.do("wait 3s")   # let the stats screen count up
    status = L.status
    L.close()

    result = (f"{status['kills']} of {status['total_kills']} kills, "
              f"{status['health']}% health left" if done else "did not reach the exit")
    try:
        shown = args.out.resolve().relative_to(doom.ROOT)
    except ValueError:
        shown = args.out
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "# All of E1M1 (Outer Prison), from the title screen to the exit switch.\n"
        f"# python3 doom.py play --title -f {shown}\n"
        "#\n"
        "# Recorded by autopilot/e1m1.py driving live mode: A* over the map's\n"
        "# geometry for the route, \"use\" at doors, and aimed pistol shots at\n"
        f"# monsters_in_sight. Result: {result}.\n\n"
        + "\n".join(MENU) + "\n\n" + "\n".join(L.moves) + "\n")
    print(f"{'Finished' if done else 'Failed'}: {result}. {len(L.moves)} moves in "
          f"{time.time() - started:.0f} s, written to {args.out}")
    sys.exit(0 if done else 1)


if __name__ == "__main__":
    main()
