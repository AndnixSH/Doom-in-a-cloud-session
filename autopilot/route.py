"""Follow an A* route through a level, opening doors on the way."""

import math

from nav import CELL, DOOR_SPECIALS, KEY_DOOR_SPECIALS, S_LIFT_SPECIALS, segs_cross


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


def through_line(m, li, p, d=24):
    """The point d units behind line li, level with p."""
    (ax, ay), (bx, by) = m.V[m.lines[li][0]], m.V[m.lines[li][1]]
    dx, dy = bx - ax, by - ay
    n = math.hypot(dx, dy)
    t = max(0.1, min(0.9, ((p[0] - ax) * dx + (p[1] - ay) * dy) / n ** 2))
    return ax + t * dx - dy / n * d, ay + t * dy + dx / n * d


def lift_ahead(m, cells, heights):
    """The first lift on the path that needs riding: (sector, first, last) cell
    indices of the run through it, or None. Dropping onto or off a lift needs
    nothing; climbing onto one, climbing off it, or getting off where the
    ceiling is too low to drop out does."""
    i = 0
    while i < len(cells):
        sec = m.cell_sector[cells[i]]
        if sec in m.lifts:
            j = i
            while j + 1 < len(cells) and m.cell_sector[cells[j + 1]] == sec:
                j += 1
            here, ceiling = heights[sec]
            # (Starting on the lift, there's no boarding to do.)
            before = m.floor_ceil(m.cell_sector[cells[i - 1]])[0] if i > 0 else here
            after, after_ceiling = (m.floor_ceil(m.cell_sector[cells[j + 1]])
                                    if j + 1 < len(cells) else (here, ceiling))
            if (before < here - 24 or after > here + 24
                    or (after < here - 24 and min(ceiling, after_ceiling) - here < 56)):
                return sec, i, j
            i = j
        i += 1
    return None


def lower_lift(L, m, sec, floor):
    """Send a lift down using one of its triggers usable from `floor`."""
    from actions import line_stand   # (actions imports route)
    s = L.status
    here = (s["x"], s["y"])
    options = []
    for li, sp in m.lifts[sec]["triggers"]:
        v1, v2 = m.V[m.lines[li][0]], m.V[m.lines[li][1]]
        if sp in S_LIFT_SPECIALS:
            stand, mid = line_stand(m, li)
            if abs(m.floor_ceil(m.sector_at(*stand))[0] - floor) <= 24:
                options.append((math.hypot(stand[0] - here[0], stand[1] - here[1]), "press", stand, mid))
        else:
            # Walk across a trigger line whose far side is at our level too;
            # from on top of the lift, step off over its trigger and back on.
            near, far = line_stand(m, li, 24)[0], line_stand(m, li, -24)[0]
            for a, b in ((near, far), (far, near)):
                sa, sb = m.sector_at(*a), m.sector_at(*b)
                if sa == sec and sb != sec and abs(m.floor_ceil(sb)[0] - floor) <= 24:
                    options.append((math.hypot(a[0] - here[0], a[1] - here[1]), "step off", a, b))
                elif sa != sec and sb != sec and all(
                        abs(m.floor_ceil(x)[0] - floor) <= 24 for x in (sa, sb)):
                    options.append((math.hypot(a[0] - here[0], a[1] - here[1]), "cross", a, b))
    for _, how, a, b in sorted(options):
        # Other lifts may be on the way to the trigger, but a trigger only
        # reachable over this same lift is no use.
        info = m.lifts.pop(sec)
        m.clear, m.drop = {}, {}
        try:
            got_there = travel(L, m, a, arrive=24, live_heights=True) == "arrived"
        finally:
            m.lifts[sec] = info
            m.clear, m.drop = {}, {}
        if not got_there:
            continue
        L.goto(*a, tol=10, final=True)
        if how == "press":
            L.settle()
            L.log("pressing a switch for the lift")
            L.use_toward(*b)
        elif how == "step off":
            L.log("stepping off over the lift's trigger line and back on")
            L.goto(*b, tol=10, final=True)
            L.goto(*a, tol=10, final=True)
        else:
            L.log("walking over the lift's trigger line")
            L.goto(*b, tol=10, final=True)
        return True
    L.log("found no way to call the lift")
    return False


def wait_lift(L, sec, cond, max_tics=400):
    for _ in range(max_tics // 5):
        if cond(L.sectors()[sec][0]):
            return True
        L.fight(600)
        L.do("wait 5t")
    return False


def ride_lift(L, m, sec, before, board, after):
    """Get from cell `before`, across lift `sec` at `board`, to cell `after`."""
    info = m.lifts[sec]
    level = lambda: L.sectors()[sec][0]
    floor_before = (level() if m.cell_sector[before] == sec      # already on it
                    else m.floor_ceil(m.cell_sector[before])[0])
    floor_after = m.floor_ceil(m.cell_sector[after])[0]
    L.log(f"riding the lift (sector {sec}, floors {info['low']}..{info['high']}) "
          f"from floor {floor_before} (sector {m.cell_sector[before]}) to {floor_after}")
    if floor_before < level() - 24:
        # We're below it: bring it down.
        if not lower_lift(L, m, sec, floor_before):
            L.log(f"planning around lift {sec} from now on")
            m.give_up_lift(sec)
            return False
        if not wait_lift(L, sec, lambda f: f <= max(floor_before + 24, info["low"] + 4)):
            L.log("the lift didn't come down")
            return False
        L.goto(*m.center(*before), tol=16, final=True, maxd=200)
    if not L.goto(*board, tol=14, final=True, maxd=200):
        L.log("couldn't get onto the lift")
        return False
    # Wait until the step off is one we can take (or the lift has gone all the way).
    if floor_after > level() + 24:
        ok = wait_lift(L, sec, lambda f: f >= min(floor_after - 24, info["high"] - 4))
    elif floor_after < level() - 24:
        L.do("wait 10t")
        if level() >= info["high"] - 4:      # stepping on didn't send it down
            lower_lift(L, m, sec, level())
            L.goto(*board, tol=14, final=True)
        ok = wait_lift(L, sec, lambda f: f <= max(floor_after + 24, info["low"] + 4))
    else:
        ok = True
    if not ok:
        L.log("the lift didn't move")
        return False
    L.goto(*m.center(*after), tol=16, final=True)
    return True


def travel(L, m, goal, arrive=30, max_legs=150, live_heights=False, on_leg=None,
           ride_lifts=True, near=0):
    """Head for goal, re-planning after every leg.

    Returns "arrived" within `arrive` units of goal, "left level" if the
    level ended on the way, or None if stuck, without a path or out of legs.
    With live_heights, the map's floors and ceilings are refreshed from the
    engine before each plan, so switched doors, lifts and lowered floors count.
    on_leg, if given, is called before each leg (to pick things up on the way).
    With ride_lifts off, a route that needs a lift ride fails instead.
    With near, getting that close to goal is enough (see Map.path).
    """
    lifts = dict(m.lifts)      # (lifts given up on are only given up for this trip)
    try:
        return _travel(L, m, goal, max(arrive, near), max_legs, live_heights, on_leg,
                       ride_lifts, near)
    finally:
        if m.lifts != lifts:
            m.lifts = lifts
            m.clear, m.drop = {}, {}


def _travel(L, m, goal, arrive, max_legs, live_heights, on_leg, ride_lifts, near):
    door_lines = [(i, m.V[l[0]], m.V[l[1]]) for i, l in enumerate(m.lines)
                  if l[3] in DOOR_SPECIALS
                  or (l[3] in KEY_DOOR_SPECIALS and l[6] != -1
                      and m.sides[l[6]][5] in m.door_sectors)]   # keys we hold
    opened = set()
    heights = None
    lift_soon = False
    for leg in range(max_legs):
        if on_leg and not lift_soon:
            on_leg()      # (not with a lift ride coming up: it may be timed)
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
        cells = m.path(here, goal, near)
        if not cells:
            L.log("no path!")
            return None
        jump = next((k for k in range(len(cells) - 1)
                     if max(abs(cells[k][0] - cells[k + 1][0]),
                            abs(cells[k][1] - cells[k + 1][1])) > 1), None)
        if jump is not None:
            entry = m.center(*cells[jump])
            if math.hypot(entry[0] - here[0], entry[1] - here[1]) < 40:
                li = m.tele_jumps[cells[jump]][cells[jump + 1]]
                L.log(f"taking the teleporter (line {li})")
                if not L.walk_through(*through_line(m, li, entry)):
                    L.log("the teleporter didn't take us anywhere")
                    return None
                continue
            cells = cells[:jump + 1]      # walk to the teleporter first
        lift = lift_ahead(m, cells, heights) if m.lifts and heights else None
        lift_soon = lift is not None and lift[1] * CELL < 800
        if lift and not ride_lifts:
            return None
        if lift:
            sec, i, j = lift
            if i > 0 and m.center(*cells[i - 1]) and math.hypot(
                    m.center(*cells[i - 1])[0] - here[0], m.center(*cells[i - 1])[1] - here[1]) > 48:
                cells = cells[:i]        # walk up to the lift first
            else:
                before = cells[i - 1] if i > 0 else m.cell(*here)
                after = cells[j + 1] if j + 1 < len(cells) else cells[j]
                if not ride_lift(L, m, sec, before, m.center(*cells[(i + j) // 2]), after):
                    if sec in m.lifts:
                        return None
                continue          # (re-plan without that lift)
        wps = m.waypoints(cells)
        ahead = [w for w in wps[1:] if math.hypot(w[0] - here[0], w[1] - here[1]) > 45]
        nxt = ahead[0] if ahead else goal
        final = len(ahead) <= 1

        cross = first_door_crossing([d for d in door_lines if d[0] not in opened], here, nxt)
        if cross:
            t, li, pt = cross
            d = math.hypot(pt[0] - here[0], pt[1] - here[1])
            L.log(f"door (line {li}) ahead at ({pt[0]:.0f},{pt[1]:.0f})")
            if d > 70:
                ux, uy = (pt[0] - here[0]) / d, (pt[1] - here[1]) / d
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
        # (Hurrying to a lift, which may only be down for a moment, only
        # monsters close by get fought.)
        if not L.goto(nxt[0], nxt[1], tol=24 if final else 40, final=final, replan=True,
                      maxd=250 if lift_soon else None):
            return None
    L.log("ran out of legs")
    return None
