"""Follow an A* route through a level, opening doors on the way."""

import math

from nav import DOOR_SPECIALS, KEY_DOOR_SPECIALS, segs_cross


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


def travel(L, m, goal, arrive=30, max_legs=60, live_heights=False, on_leg=None):
    """Head for goal, re-planning after every leg.

    Returns "arrived" within `arrive` units of goal, "left level" if the
    level ended on the way, or None if stuck, without a path or out of legs.
    With live_heights, the map's floors and ceilings are refreshed from the
    engine before each plan, so switched doors, lifts and lowered floors count.
    on_leg, if given, is called before each leg (to pick things up on the way).
    """
    door_lines = [(i, m.V[l[0]], m.V[l[1]]) for i, l in enumerate(m.lines)
                  if l[3] in DOOR_SPECIALS
                  or (l[3] in KEY_DOOR_SPECIALS and l[6] != -1
                      and m.sides[l[6]][5] in m.door_sectors)]   # keys we hold
    opened = set()
    heights = None
    for leg in range(max_legs):
        if on_leg:
            on_leg()
        s = L.status
        if s["state"] != "level":
            return "left level"
        here = (s["x"], s["y"])
        if math.hypot(goal[0] - here[0], goal[1] - here[1]) < arrive:
            return "arrived"
        if live_heights:
            now = L.sectors()
            if now != heights:
                m.update_heights(now)
                heights = now
        cells = m.path(here, goal)
        if not cells:
            L.log("no path!")
            return None
        wps = m.waypoints(cells)
        ahead = [w for w in wps[1:] if math.hypot(w[0] - here[0], w[1] - here[1]) > 45]
        nxt = ahead[0] if ahead else goal
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
            return None
    L.log("ran out of legs")
    return None
