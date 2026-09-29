"""Drive a running Doom through live mode, recording every move.

Live replays a list of starting moves, then keeps the game paused between
calls to do(). Everything sent through do() is ordinary move text, so the
recording in .moves replays identically with `doom.py play`.
"""

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import doom  # noqa: E402


def angnorm(a):
    return (a + 180) % 360 - 180


def bearing(x0, y0, x1, y1):
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360


class Died(Exception):
    pass


MELEE = {"demon", "spectre", "lost soul"}
PROJECTILES = {"imp", "cacodemon", "baron of hell", "hell knight", "arachnotron",
               "mancubus", "revenant"}
WEAPON_KEYS = {"pistol": "2", "shotgun": "3", "chaingun": "4", "rocket launcher": "5"}
AMMO_OF = {"pistol": "bullets", "shotgun": "shells", "chaingun": "bullets",
           "rocket launcher": "rockets"}


class Live:
    def __init__(self, wad, prefix_moves, warp_args=(), verbose=True, weapon_prefs=()):
        """Start Doom, play prefix_moves, and pause at their end.

        warp_args are extra Doom arguments, e.g. ["-warp", "1", "1", "-skill", "3"];
        leave them out to start at the title screen. weapon_prefs, e.g.
        ["shotgun", "chaingun", "pistol"], makes fights switch to the first
        weapon owned with ammo; by default the current weapon is kept.
        """
        self.wad = wad
        self.prefix = list(prefix_moves)
        self.warp_args = list(warp_args)
        self.verbose = verbose
        self.weapon_prefs = list(weapon_prefs)
        self.kite = False     # back away from melee monsters between shots
        self.dodge = False    # strafe between shots at monsters that throw fireballs
        self.engage = 900     # how far away a monster in sight gets shot at while moving
        self.moves = []
        self._start()

    def _start(self):
        events, end = doom.compile_moves("; ".join(self.prefix + self.moves))
        self.tmp = tempfile.TemporaryDirectory(prefix="doom-live-")
        keys = Path(self.tmp.name) / "prefix.txt"
        keys.write_text("".join(f"{t} {p} {k}\n" for t, p, k in events))
        cmd = [doom.BINARY, "-iwad", self.wad, "-keys", keys, "-maxtics", end,
               "-interactive", *self.warp_args]
        self.p = subprocess.Popen([str(c) for c in cmd], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, cwd=self.tmp.name)
        self.tic = end
        self.status = self._read()

    def rewind(self, n):
        """Go back to just after the first n recorded moves (by replaying them)."""
        self.close()
        self.moves = self.moves[:n]
        self._start()

    def _query(self, command):
        self.p.stdin.write(command + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("doom exited")
            if line.startswith('{"' + command + '"'):
                return json.loads(line)[command]

    def sectors(self):
        """Current [floor, ceiling] of every sector."""
        return self._query("sectors")

    def items(self):
        """Pickups still in the level: [{"type": doomednum, "x": .., "y": ..}]."""
        return self._query("items")

    def _read(self):
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("doom exited")
            if line.startswith("{"):
                return json.loads(line)

    def do(self, text):
        """Play some move text from the current tic and return the new status."""
        events, end = doom.compile_moves(text, tic=self.tic)
        for t, pr, k in events:
            self.p.stdin.write(f"key {t} {pr} {k}\n")
        self.p.stdin.write(f"until {end}\n")
        self.p.stdin.flush()
        self.tic = end
        self.moves.append(text)
        self.status = self._read()
        return self.status

    def close(self):
        try:
            self.p.stdin.write("quit\n")
            self.p.stdin.flush()
        except BrokenPipeError:
            pass
        self.p.wait()
        self.tmp.cleanup()

    def log(self, *args):
        if self.verbose:
            s = self.status
            print(f"[tic {s['tic']:5d} health {s['health']:3d} ammo {s['ammo']:3d} "
                  f"kills {s['kills']:2d} at ({s['x']},{s['y']}) {s['angle']:.0f}°]",
                  *args, flush=True)

    # ----- actions -----

    def aim_text(self, target_deg):
        d = angnorm(target_deg - self.status["angle"])
        if abs(d) < 0.3:
            return ""
        return f"aim {'left' if d > 0 else 'right'} {min(abs(d), 179.9):.1f}; "

    def threats(self, maxd):
        s = self.status
        out = []
        for m in s["monsters_in_sight"]:
            d = math.hypot(m["x"] - s["x"], m["y"] - s["y"])
            if d < maxd:
                out.append((d, m))
        return sorted(out, key=lambda t: t[0])

    def pick_weapon(self, dist=0):
        """Switch to the preferred weapon, if weapon_prefs asks for it.

        Beyond 350 units the shotgun's spread wastes shells, so it's skipped
        for a weapon with aimed bullets if there is one.
        """
        s = self.status
        prefs = self.weapon_prefs
        if dist > 350 and any(w in s["weapons"] and w != "shotgun" for w in prefs):
            prefs = [w for w in prefs if w != "shotgun"]
        for w in prefs:
            if w in s["weapons"] and s["ammo_all"][AMMO_OF[w]] > 0:
                if s["weapon"] != w:
                    self.do(f"tap {WEAPON_KEYS[w]}; wait 30t")   # lower one, raise the other
                return

    def fight(self, maxd=900):
        """Shoot the nearest monster in sight until none are left within maxd."""
        fired = 0
        while True:
            if self.status["dead"]:
                raise Died()
            ts = self.threats(maxd)
            if not ts or fired > 60:
                return fired
            d, m = ts[0]
            if fired == 0:
                self.log(f"engaging {m['type']} at {d:.0f}")
            if self.weapon_prefs:
                self.pick_weapon(d)
            s = self.status
            aim = self.aim_text(bearing(s["x"], s["y"], m["x"], m["y"]))
            if s["weapon"] == "chaingun":
                self.do(aim + "hold fire 8t")
            else:
                # One aimed shot per weapon cycle: a pistol shot fired from rest
                # goes where you aim, a held trigger sprays.
                cycle = 37 if s["weapon"] == "shotgun" else 20
                if self.kite and m["type"] in MELEE and d < 250:
                    # Demons bite; a running player outpaces them.
                    self.do(aim + f"hold fire 4t; hold back+run {cycle - 5}t")
                elif self.dodge and m["type"] in PROJECTILES:
                    # Fireballs are slow enough to sidestep.
                    side = "strafeleft" if fired % 2 else "straferight"
                    self.do(aim + f"hold fire 4t; hold {side}+run {cycle - 5}t")
                else:
                    self.do(aim + f"hold fire 4t; wait {cycle - 5}t")
            fired += 1
            if self.status["ammo"] == 0:
                if self.weapon_prefs:
                    self.pick_weapon(d)
                    if self.status["ammo"] != 0:
                        continue
                self.log("out of ammo!")
                return fired

    def goto(self, x, y, tol=24, final=True, maxd=None):
        """Head for (x, y), fighting on the way. Returns False if stuck."""
        if maxd is None:
            maxd = self.engage
        last = None
        stuck = 0
        bumps = 0
        for _ in range(200):
            if self.status["dead"]:
                raise Died()
            self.fight(maxd)
            s = self.status
            dist = math.hypot(x - s["x"], y - s["y"])
            if dist <= tol:
                return True
            aim = self.aim_text(bearing(s["x"], s["y"], x, y))
            if dist > 220 or not final:
                n = max(3, min(12, int((dist - 100) / 16.7)))
                self.do(aim + f"hold forward+run {n}t")
            else:
                n = max(2, min(8, int(dist / 12)))
                self.do(aim + f"hold forward {n}t; wait 4t")
            s2 = self.status
            if last and math.hypot(s2["x"] - last[0], s2["y"] - last[1]) < 3:
                stuck += 1
                bumps += 1
                if bumps >= 6:
                    self.log(f"keeps getting stuck heading to ({x},{y})")
                    return False
                if stuck == 2:
                    self.log("bumped into something: trying use")  # a door that shut again?
                    self.use_toward(x, y)
                elif stuck == 3:
                    self.log("still stuck: sidestepping")
                    self.do("hold strafeleft 6t" if (s2["tic"] // 7) % 2
                            else "hold straferight 6t")
                elif stuck >= 5:
                    self.log(f"stuck heading to ({x},{y})")
                    return False
            else:
                stuck = 0
            last = (s2["x"], s2["y"])
        return False

    def settle(self):
        self.do("wait 12t")

    def use_toward(self, x, y):
        s = self.status
        self.do(self.aim_text(bearing(s["x"], s["y"], x, y)) + "tap use; wait 1s")
