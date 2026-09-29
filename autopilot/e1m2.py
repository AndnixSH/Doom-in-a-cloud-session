#!/usr/bin/env python3
"""Play Freedoom's E1M2 straight after E1M1, from the E1M1 stats screen to the exit.

    python3 autopilot/e1m2.py                                  # writes out/e1m2-autopilot.txt
    python3 autopilot/e1m2.py --out examples/e1m2-complete.txt

It carries on from examples/e1m1-complete.txt, so it starts the level the way
E1M1 left the player: 8% health, a pistol, and a shotgun with 4 shells. The
level needs all three keycards; the route is written out below, and the
autopilot (route.py, live.py, actions.py) handles the walking, doors,
pickups on the way, and the fighting. The run it writes replays with

    python3 doom.py play --title -f examples/e1m1-complete.txt -f examples/e1m2-complete.txt
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

TO_E1M2 = ["tap use", "wait 1s", "tap use", "wait 1s", "tap use", "wait 2s"]
BLUE, YELLOW, RED = (26, 32), (27, 34), (28, 33)   # door specials each key opens


def play(r):
    L = r.L

    # The start room: shoot what's in sight from the start, then a clip, the
    # shotgun, three health bonuses, the green armor and shells past the fast door.
    L.fight(900)
    r.collect(464, -16)
    r.collect(672, -616)
    for x in (944, 992, 1040):
        r.collect(x, -392)
    r.collect(1000, -1176)
    for x in (972, 940, 908):
        r.collect(x, -1220)

    # Blue key: the switch on line 219 lowers the lift up to the floor-240
    # room; take the stimpacks there, then a switch lowers the key's alcove.
    r.ride_lift_up(stand=(1305, -1408), switch_point=(1344, -1408), sector=44,
                   board=(1420, -1408), low=136, high=240)
    L.fight(900)
    r.collect(1800, -1456)
    r.collect(1800, -1488)
    r.press((1664, -996), (1664, -960), "the key alcove switch")
    L.do("wait 1s")
    r.collect(1504, -960, "blue key")       # opens closets: a demon and imps
    L.fight(900)
    L.do("wait 1s")
    L.fight(900)
    r.m.set_keys(BLUE)

    # Back down the lift, and through the blue door to a medikit.
    r.ride_lift_down(approach=(1420, -1290), board=(1420, -1408), sector=44,
                     low=136, off=(1290, -1408))
    r.collect(672, -1520, "medikit")

    # Walking over line 1556 opens two doors (tag 16), freeing two demons and
    # imps. Health bonuses first, then back away from the demons while shooting.
    L.kite = True
    for y in (-1970, -2010, -2050, -2090, -2130):
        r.collect(1040, y)
    for x in (1295, 1335, 1375, 1415, 1455):
        r.collect(x, -2097)
    r.collect(900, -2272, "the tag-16 door trigger")
    L.fight(900)
    L.do("wait 1s")
    L.fight(900)

    # Red key: the switch behind those doors opens the bars (tag 17) on the
    # way east; round the south-east rooms to the star room. The key sits on a
    # ledge too high to climb, but can be reached from its edge.
    r.press_line(1854, "the bars switch")
    L.do("wait 1s")
    r.go(1568, -2176, "the chaingun")
    r.go(672, -2926, "red key")
    L.fight(900)
    r.m.set_keys(BLUE + RED)

    # All the way back north to the red door by the start; the switch in the
    # red room (line 93) opens the way north-east.
    r.go(*r.line_stand(93)[0], name="the red room switch")
    r.press_line(93, "the red room switch")
    L.do("wait 1s")
    L.fight(900)
    r.go(792, 184, "north alcove stimpack")

    # A hall full of imps guards the north-east. Hurry through to the
    # soulsphere, only shooting what's close and sidestepping fireballs.
    L.engage = 400
    L.dodge = True
    r.go(*r.line_stand(1284)[0], name="the soulsphere switch")
    r.press_line(1284, "the soulsphere switch")
    L.do("wait 1s")
    r.go(2144, 832, "the soulsphere")
    L.engage = 900
    L.dodge = False
    L.fight(900)

    # Yellow key on its column (switch on line 1904), then the yellow door
    # and the exit switch.
    r.press_line(1904, "the yellow key switch")
    L.do("wait 1s")
    r.go(2272, 768, "yellow key")
    L.fight(900)
    r.m.set_keys(BLUE + RED + YELLOW)
    r.go(*r.line_stand(737)[0], name="the exit switch")
    r.press_line(737, "the exit switch")
    L.do("wait 1s")
    L.log("state now", L.status["state"])
    return L.status["state"] != "level"


def moves_in(path):
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=doom.ROOT / "out" / "e1m2-autopilot.txt",
                        help="moves file to write (default: out/e1m2-autopilot.txt)")
    parser.add_argument("--after", type=Path, default=doom.ROOT / "examples" / "e1m1-complete.txt",
                        help="the E1M1 run to continue from (default: examples/e1m1-complete.txt)")
    parser.add_argument("-q", "--quiet", action="store_true", help="don't log each step")
    args = parser.parse_args()

    wad = doom.WADS / "freedoom1.wad"
    if not doom.BINARY.exists() or not wad.exists():
        doom.build()

    started = time.time()
    L = Live(wad, moves_in(args.after) + TO_E1M2, verbose=not args.quiet,
             weapon_prefs=["shotgun", "chaingun", "pistol"])
    r = Run(L, Map(wad, "E1M2"))
    try:
        done = play(r)
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

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "# All of E1M2, straight after E1M1, to the exit switch. Replay with\n"
        f"# python3 doom.py play --title -f {shown(args.after)} -f {shown(args.out)}\n"
        "#\n"
        "# Recorded by autopilot/e1m2.py driving live mode: a scripted route\n"
        "# through the three keys, with A* for the walking, pickups on the way,\n"
        f"# and aimed shots at monsters_in_sight. Result: {result}.\n\n"
        + "\n".join(TO_E1M2) + "\n\n" + "\n".join(L.moves) + "\n")
    print(f"{'Finished' if done else 'Failed'}: {result}. {len(L.moves)} moves in "
          f"{time.time() - started:.0f} s, written to {args.out}")
    sys.exit(0 if done else 1)


if __name__ == "__main__":
    main()
