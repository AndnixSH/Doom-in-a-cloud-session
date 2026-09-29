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
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import doom  # noqa: E402
from live import Died, Live  # noqa: E402
from nav import Map  # noqa: E402
from route import travel  # noqa: E402

# Title screen -> New Game -> Episode 1 -> the default skill.
MENU = ["wait 1.5s", "tap esc", "wait 0.6s", "tap enter", "wait 0.6s",
        "tap enter", "wait 0.6s", "tap enter", "wait 1.5s"]
GOAL = (-360, 1296)     # in front of the exit switch
SWITCH = (-420, 1296)   # the switch itself, on the west wall


def run(L, m):
    result = travel(L, m, GOAL)
    if result != "arrived":
        return result == "left level"
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
