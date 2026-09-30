"""Play a level from a short route: switches to press, things to get, the exit.

The route says only what a player needs to do in the level, in order; the
rest (walking there, doors, lifts, picking up health and ammo nearby, and
fighting) is the same for every level. Each level's script is a route plus
a call to main():

    ROUTE = [("press", 361, "the door switch"), ("get", (432, 2608), "blue key"),
             ("cross", 1574, "the trigger line"), ("do", "wait 2s"), ("exit", 1367)]
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


class Checkpoints:
    """Moments to go back to after dying, as a tool-assisted run would.

    The game is deterministic, so a retry from the same moment only goes
    differently if something changes: each retry first waits a few tics,
    which is enough to change what the monsters do. A checkpoint is dropped
    after three failed retries, falling back to the one before it.
    """

    def __init__(self, L, gap=350, budget=150):
        self.L = L
        self.gap = gap          # tics between checkpoints while walking
        self.budget = budget    # retries allowed in the whole level
        self.step = 0           # the route step being done
        self.saved = []
        self.stuck = set()      # steps that failed without dying (no new checkpoints in them)

    def mark(self, force=False):
        L, s = self.L, self.L.status
        if s["dead"] or s["state"] != "level" or (not force and (
                s["health"] < 25 or self.step in self.stuck)):
            return
        last = self.saved[-1] if self.saved else None
        if last and (last["n"] == len(L.moves) or (
                not force and last["step"] == self.step and s["tic"] - last["tic"] < self.gap)):
            return
        self.saved.append(dict(n=len(L.moves), tic=s["tic"], step=self.step, tries=0,
                               health=s["health"]))

    # Retries also fight differently: (back off with this many in sight,
    # walk past fireball throwers further than this, hurry past everything).
    TACTICS = [(3, 450, False), (2, 350, False), (99, 600, True), (4, 300, False), (3, 450, True)]

    def tries_for(self, i):
        """Retries for checkpoint i: one if nearly dead and there's a
        healthier one to fall back to, more for the first (nothing before it)."""
        cp = self.saved[i]
        if cp["health"] < 30 and any(c["health"] >= 30 for c in self.saved[:i]):
            return 1
        return 6 if i == 0 else 3

    def back(self):
        """Rewind to the latest checkpoint with retries left; returns its step."""
        while self.saved and self.saved[-1]["tries"] >= self.tries_for(len(self.saved) - 1):
            self.saved.pop()
        if not self.saved or self.budget <= 0:
            return None
        cp = self.saved[-1]
        cp["tries"] += 1
        self.budget -= 1
        self.L.rewind(cp["n"])
        self.L.back_off, self.L.pass_by, self.L.hurry = self.TACTICS[cp["tries"] % len(self.TACTICS)]
        self.L.do(f"wait {7 * cp['tries']}t")
        self.L.log(f"rewound to tic {cp['tic']} (route step {cp['step']}), retry {cp['tries']}")
        return cp["step"]


def do_step(r, step):
    """Do one step of a route. Returns False if it couldn't be done."""
    L = r.L
    kind, what = step[0], step[1]
    name = step[2] if len(step) > 2 else ""
    grab = (step[3] if len(step) > 3 else {}).get("grab", True)   # pickups on the way?
    if kind == "press":
        stand, mid = r.line_stand(what)
        ok = (r.go(*stand, name=name or f"switch {what}", grab=grab)
              and r.press_line(what, name or f"switch {what}"))
        L.do("wait 1s")
    elif kind == "cross":
        # Walk over a trigger line, from whichever side is nearer by the map
        # (the far side can be somewhere to walk at but not stand in).
        (a, _), (b, _) = r.line_stand(what, 24), r.line_stand(what, -24)
        here = (L.status["x"], L.status["y"])
        to_a, to_b = r.m.path(here, a), r.m.path(here, b)
        if to_b and (not to_a or len(to_b) < len(to_a)):
            a, b = b, a
        ok = r.go(*a, name=name or f"line {what}", grab=grab)
        if ok:
            L.goto(*b, tol=10, final=True)
    elif kind == "get":
        ok = r.go(*what, name=name, near=40, grab=grab)
    elif kind == "do":
        # Plain moves, e.g. a shot to wake monsters up.
        if name:
            L.log(name)
        L.do(what)
        ok = True
    elif kind == "exit":
        if r.m.lines[what][3] in (52, 124):          # an exit line to walk over
            (a, _), (b, _) = r.line_stand(what, 24), r.line_stand(what, -24)
            r.go(*a, name="the exit") and L.goto(*b, tol=10, final=True)
        else:                                        # an exit switch
            stand, mid = r.line_stand(what)
            r.go(*stand, name="the exit") and r.press_line(what, "the exit switch")
        L.do("wait 1s")
        ok = L.status["state"] != "level"
    L.fight(900)
    return ok


def follow(r, route, start=0, progress=None):
    """Do each step of the route, going back to a checkpoint after dying or
    failing a step. Returns True once the level is over.

    progress, if given, is called with the step number after each step done.
    """
    L = r.L
    cps = Checkpoints(L)
    L.on_calm = cps.mark
    i, fresh = start, True
    while i < len(route):
        cps.step = i
        if fresh:
            cps.mark(force=True)
        died = False
        try:
            ok = do_step(r, route[i])
        except Died:
            died = True
            L.log("died; just before:")
            for h in L.history:
                L.log(f"    tic {h['tic']} health {h['health']} at ({h['x']},{h['y']}) sees " + ", ".join(
                    f"{m['type']} ({m['x']},{m['y']}) {m['health']}hp" for m in h["monsters_in_sight"]))
            ok = False
        r.m.set_keys(sum((KEY_DOORS[k] for k in L.status["keys"]), ()))
        if L.status["state"] != "level" and not L.status["dead"]:
            return True
        if ok:
            cps.stuck.discard(i)
            L.back_off, L.pass_by, L.hurry = Checkpoints.TACTICS[0]
            L.log(f"step {i} done after {len(L.moves)} moves")
            if progress:
                progress(i)
        if not ok:
            if not died:
                cps.stuck.add(i)    # (retrying won't get further by checkpointing on)
            back = cps.back()
            if back is None:
                L.log("out of retries")
                return False
            r.m.set_keys(sum((KEY_DOORS[k] for k in L.status["keys"]), ()))
            r.skip.clear()          # (what was out of reach then may not be now)
            i, fresh = back, False
            continue
        i, fresh = i + 1, True
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
    parser.add_argument("--warp", action="store_true",
                        help="try the route from a pistol start instead (for testing)")
    parser.add_argument("--resume", nargs=3, metavar=("FILE", "MOVES", "STEP"),
                        help="carry on from the first MOVES moves of an earlier run's FILE, "
                             "at route step STEP (the log says which move each step ended on)")
    args = parser.parse_args()

    wad = doom.WADS / "freedoom1.wad"
    if not doom.BINARY.exists() or not wad.exists():
        doom.build()
    before = [doom.ROOT / "examples" / f for f in after]
    if args.warp:
        before, prefix, warp = [], [], ["-warp", mapname[1], mapname[3], "-skill", "3"]
    else:
        prefix = [m for path in before for m in moves_in(path)] + TO_NEXT_LEVEL
        warp = []

    earlier, first = [], 0
    if args.resume:
        earlier = moves_in(Path(args.resume[0]))[len(TO_NEXT_LEVEL):][:int(args.resume[1])]
        first = int(args.resume[2])

    started = time.time()
    L = Live(wad, prefix + earlier, warp_args=warp, verbose=not args.quiet,
             weapon_prefs=["shotgun", "chaingun", "plasma rifle", "rocket launcher", "pistol"])
    L.prefix, L.moves = prefix, earlier
    L.kite = True
    L.dodge = True
    m = Map(wad, mapname, lifts=True, avoid_hurt=True, one_way_doors=True)
    L.safe_move = m.safe_walk
    L.near_drop = m.near_drop
    m.set_keys(sum((KEY_DOORS[k] for k in L.status["keys"]), ()))
    r = Run(L, m)
    def save_progress(step):
        # (so a later run can --resume from here: FILE MOVES STEP+1)
        partial = args.out.with_suffix(".partial")
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text(f"# {mapname}: {len(L.moves)} moves, route step {step} done\n"
                           + "\n".join(TO_NEXT_LEVEL + L.moves) + "\n")

    done = follow(r, route, first, save_progress)
    if done:
        # Let the stats screen count up (or, after E1M8, the story text show).
        L.do("wait 20s" if L.status["state"] == "finale" else "wait 3s")
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
