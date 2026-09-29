"""Level-independent things to do with a live game: pick things up, press
switches, ride lifts. A Run ties a Live game (live.py) to its Map (nav.py).
"""

import math

from route import travel


def line_stand(m, li, dist=34):
    """A spot `dist` in front of line li's front (usable) side, and its middle."""
    v1, v2 = m.V[m.lines[li][0]], m.V[m.lines[li][1]]
    mx, my = (v1[0] + v2[0]) / 2, (v1[1] + v2[1]) / 2
    dx, dy = v2[0] - v1[0], v2[1] - v1[1]
    n = math.hypot(dx, dy)
    return (mx + dy / n * dist, my - dx / n * dist), (mx, my)

HEALTH = {2011, 2012}             # stimpack, medikit
ALWAYS = {2013, 2014, 2015, 8}    # soulsphere, health bonus, armor bonus, backpack


def wanted(thing_type, s):
    """Is a pickup of this type worth a detour, given the player's status?"""
    h, a, ammo = s["health"], s["armor"], s["ammo_all"]
    if thing_type in HEALTH:
        return h < 100
    if thing_type in ALWAYS:
        return True
    if thing_type == 2018:                   # green armor
        return a < 100
    if thing_type == 2019:                   # blue armor
        return a < 200
    if thing_type in (2008, 2049, 2001):     # shells, box of shells, shotgun
        return ammo["shells"] < 50
    if thing_type in (2007, 2048):           # clip, box of bullets
        return ammo["bullets"] < 150
    if thing_type == 2002:                   # chaingun
        return "chaingun" not in s["weapons"] or ammo["bullets"] < 150
    return False


class Run:
    def __init__(self, L, m):
        self.L = L
        self.m = m
        self.skip = set()   # pickups found to be out of reach

    def collect(self, x, y, name="", ride_lifts=True):
        """Walk to (x, y) and stand on it."""
        L = self.L
        r = travel(L, self.m, (x, y), arrive=40, live_heights=True, ride_lifts=ride_lifts,
                   near=40)
        if r != "arrived":
            L.log(f"could not reach {name} ({x},{y}): {r}")
            return False
        L.goto(x, y, tol=10, final=True)
        L.log(f"collected {name}" if name else f"at ({x},{y})")
        return True

    def go(self, x, y, name="", near=0):
        """Like collect(), but picking up useful things near the way."""
        L = self.L
        r = travel(L, self.m, (x, y), arrive=40, live_heights=True,
                   on_leg=self.grab_nearby, near=near)
        if r != "arrived":
            L.log(f"could not reach {name} ({x},{y}): {r}")
            return False
        L.goto(x, y, tol=10, final=True)
        if name:
            L.log(f"reached {name}")
        return True

    def grab_nearby(self, radius=400):
        """Detour for wanted pickups within `radius` that have a short path
        (and for health from further away when running low)."""
        L, m = self.L, self.m
        for _ in range(6):
            s = L.status
            if s["state"] != "level":
                return
            here = (s["x"], s["y"])

            def reach(it):
                if it["type"] in HEALTH | ALWAYS and s["health"] < 50:
                    return radius * (6 if s["health"] < 25 else 2.5)
                return radius

            cands = sorted(((math.hypot(it["x"] - here[0], it["y"] - here[1]), it)
                            for it in L.items()
                            if wanted(it["type"], s) and (it["x"], it["y"]) not in self.skip),
                           key=lambda c: c[0])
            cands = [c for c in cands if c[0] < reach(c[1])][:3]
            got = False
            for d, it in cands:
                cells = m.path(here, (it["x"], it["y"]))
                if not cells or m.cell_sector[cells[-1]] != m.sector_at(it["x"], it["y"]):
                    self.skip.add((it["x"], it["y"]))
                    continue
                if len(cells) * 16 > 1.8 * reach(it):
                    continue            # too far round for now
                self.collect(it["x"], it["y"], ride_lifts=False)
                if it in L.items():
                    self.skip.add((it["x"], it["y"]))    # still there: out of reach
                got = True
                break
            if not got:
                return

    def line_stand(self, li, dist=34):
        """A spot on the front (usable) side of line li, and the line's middle."""
        return line_stand(self.m, li, dist)

    def press(self, stand, switch_point, name=""):
        """Go to `stand` and press the switch at `switch_point`."""
        L = self.L
        if travel(L, self.m, stand, arrive=30, live_heights=True) != "arrived":
            L.log(f"could not reach switch {name}")
            return False
        L.goto(*stand, tol=10, final=True)
        L.settle()
        L.fight(900)
        L.log(f"pressing {name}")
        L.use_toward(*switch_point)
        return True

    def press_line(self, li, name=""):
        stand, mid = self.line_stand(li)
        return self.press(stand, mid, name or f"line {li}")

    def wait_floor(self, sector, cond, max_tics=400, fight=600):
        """Wait (fighting) until cond(floor height of sector) holds."""
        L = self.L
        for _ in range(max_tics // 5):
            if cond(L.sectors()[sector][0]):
                return True
            if fight:
                L.fight(fight)
            L.do("wait 5t")
        return False

    def ride_lift_up(self, stand, switch_point, sector, board, low, high):
        """Lower a lift with its switch, step on, and wait for it to rise."""
        L = self.L
        if travel(L, self.m, stand, arrive=30, live_heights=True) != "arrived":
            L.log("could not reach the lift switch")
            return False
        L.goto(*stand, tol=10, final=True)
        L.settle()
        L.fight(900)
        L.log("pressing the lift switch")
        L.use_toward(*switch_point)
        if not self.wait_floor(sector, lambda f: f <= low + 4):
            L.log("lift did not come down")
            return False
        L.goto(*board, tol=14, final=True)
        L.log("on the lift, waiting to rise")
        if not self.wait_floor(sector, lambda f: f >= high):
            L.log("lift did not rise")
            return False
        L.log("lift is up")
        return True

    def ride_lift_down(self, approach, board, sector, low, off):
        """Step onto a lift over its walk-over line, ride it down, step off."""
        L = self.L
        if travel(L, self.m, approach, arrive=30, live_heights=True) != "arrived":
            L.log("could not reach the lift")
            return False
        L.goto(*approach, tol=12, final=True)
        L.goto(*board, tol=14, final=True)
        L.log("on the lift, riding down")
        if not self.wait_floor(sector, lambda f: f <= low + 4):
            L.log("lift did not come down")
            return False
        L.goto(*off, tol=16, final=True)
        L.log("off the lift")
        return True
