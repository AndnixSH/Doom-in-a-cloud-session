"""Pathfinding over a Doom map, read straight from the WAD.

The map is cut into a 16-unit grid. Each cell gets its sector by walking the
map's BSP tree, the same way the engine does, and A* searches the grid with
Doom's movement rules: steps up of at most 24 units, 56 units of headroom,
walls and blocking lines kept at arm's length, and ledges you can drop off
but not climb. Doors anyone can open count as open.
"""

import heapq
import math
import struct

CELL = 16
RADIUS = 18                                  # player radius 16, plus a little margin
DOOR_SPECIALS = {1, 31, 117, 118}            # doors anyone can open
S_LIFT_SPECIALS = {21, 62, 122, 123}         # switches that lower a lift
W_LIFT_SPECIALS = {10, 88, 120, 121}         # lines that lower a lift when crossed
HURT_SPECIALS = {4, 5, 7, 11, 16}            # sector specials for damaging floors
KEY_DOOR_SPECIALS = {26, 27, 28, 32, 33, 34}
TELEPORT_SPECIALS = {39, 97}                 # W1 and WR teleporters (for players too)


def read_map(wad_path, mapname):
    """The lumps of one map, as lists of tuples."""
    wad = open(wad_path, "rb").read()
    count, offset = struct.unpack_from("<ii", wad, 4)
    directory = [struct.unpack_from("<ii8s", wad, offset + 16 * i) for i in range(count)]
    names = [name.rstrip(b"\0").decode("ascii", "replace") for _, _, name in directory]
    start = names.index(mapname)

    def lump(name, fmt):
        pos, size, _ = next(directory[j] for j in range(start + 1, start + 11)
                            if names[j] == name)
        data = wad[pos:pos + size]
        step = struct.calcsize(fmt)
        return [struct.unpack_from(fmt, data, k) for k in range(0, len(data), step)]

    return {
        "vertexes": lump("VERTEXES", "<hh"),
        "linedefs": lump("LINEDEFS", "<hhhhhhh"),     # v1 v2 flags special tag side1 side2
        "sidedefs": lump("SIDEDEFS", "<hh8s8s8sh"),   # ... sector
        "sectors": lump("SECTORS", "<hh8s8shhh"),     # floor ceiling ...
        "things": lump("THINGS", "<hhhhh"),
        "nodes": lump("NODES", "<hhhhhhhhhhhhHH"),
        "ssectors": lump("SSECTORS", "<hh"),
        "segs": lump("SEGS", "<hhhhhh"),
    }


class Map:
    def __init__(self, wad_path, mapname, keys=(), lifts=False, avoid_hurt=False,
                 one_way_doors=False):
        lumps = read_map(wad_path, mapname)
        self.V = lumps["vertexes"]
        self.lines = lumps["linedefs"]
        self.sides = lumps["sidedefs"]
        self.sectors = lumps["sectors"]
        self.things = lumps["things"]
        self.nodes = lumps["nodes"]
        self.ssectors = lumps["ssectors"]
        self.segs = lumps["segs"]

        self.lifts = self._find_lifts() if lifts else {}
        self.one_way_doors = one_way_doors
        # Damaging floors (nukage and the like) cost extra to cross, if asked.
        self.hurt = ({i for i, sec in enumerate(self.sectors) if sec[5] in HURT_SPECIALS}
                     if avoid_hurt else set())
        self.set_keys(keys)

        # Lines bucketed into 128-unit blocks, padded by a block each way.
        self.B = 128
        self.buckets = {}
        for idx, (v1, v2, fl, sp, tag, s1, s2) in enumerate(self.lines):
            (ax, ay), (bx, by) = self.V[v1], self.V[v2]
            for bx_ in range(min(ax, bx) // self.B - 1, max(ax, bx) // self.B + 2):
                for by_ in range(min(ay, by) // self.B - 1, max(ay, by) // self.B + 2):
                    self.buckets.setdefault((bx_, by_), []).append(idx)

        xs = [v[0] for v in self.V]
        ys = [v[1] for v in self.V]
        self.x0, self.y0 = min(xs), min(ys)
        self.nx = (max(xs) - self.x0) // CELL + 1
        self.ny = (max(ys) - self.y0) // CELL + 1
        self.cell_sector = {}
        for gx in range(self.nx):
            for gy in range(self.ny):
                self.cell_sector[gx, gy] = self.sector_at(*self.center(gx, gy))
        self.clear = {}   # cell -> (distance to walls, distance to step-ups)
        self.drop = {}    # cell -> distance to drop-offs
        self._find_teleports()

    def _find_lifts(self):
        """Lift sectors: {sector: {"low", "high", "triggers": [(line, special)]}}.

        A lift rests at its own floor and lowers to its lowest neighbour's.
        With lifts on, the planner treats a lift as a link between the two:
        it can be boarded at the bottom and left at either level.
        """
        by_tag = {}
        for idx, l in enumerate(self.lines):
            if l[4] and l[3] in S_LIFT_SPECIALS | W_LIFT_SPECIALS:
                by_tag.setdefault(l[4], []).append((idx, l[3]))
        neighbours = {}
        for l in self.lines:
            if l[6] != -1:
                a, b = self.sides[l[5]][5], self.sides[l[6]][5]
                if a != b:
                    neighbours.setdefault(a, set()).add(b)
                    neighbours.setdefault(b, set()).add(a)
        lifts = {}
        for i, sec in enumerate(self.sectors):
            if sec[6] in by_tag:
                high = sec[0]
                low = min([self.sectors[n][0] for n in neighbours.get(i, ())] + [high])
                if high - low > 24:
                    lifts[i] = {"low": low, "high": high, "triggers": by_tag[sec[6]]}
        return lifts

    def _find_teleports(self):
        """Teleporter lines work when crossed from the front. The planner
        treats the cells in front as a jump to the destination instead of a
        step across the line.

        tele_jumps: {cell in front: {destination cell: line}}
        tele_blocked: {(cell in front, cell behind)}: steps that would teleport
        """
        dests = {}
        for x, y, a, t, f in self.things:
            if t == 14:                          # teleport destination
                dests.setdefault(self.sectors[self.sector_at(x, y)][6], (x, y))
        self.tele_jumps = {}
        self.tele_blocked = set()
        for idx, (v1, v2, fl, sp, tag, s1, s2) in enumerate(self.lines):
            if sp not in TELEPORT_SPECIALS or tag not in dests or s2 == -1:
                continue
            (ax, ay), (bx, by) = self.V[v1], self.V[v2]
            dest = self.cell(*dests[tag])
            ga, gb = self.cell(ax, ay), self.cell(bx, by)
            for gx in range(min(ga[0], gb[0]) - 2, max(ga[0], gb[0]) + 3):
                for gy in range(min(ga[1], gb[1]) - 2, max(ga[1], gb[1]) + 3):
                    x1, y1 = self.center(gx, gy)
                    if (bx - ax) * (y1 - ay) - (by - ay) * (x1 - ax) >= 0:
                        continue                 # not in front of the line
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            x2, y2 = self.center(gx + dx, gy + dy)
                            if (dx or dy) and segs_cross(x1, y1, x2, y2, ax, ay, bx, by):
                                self.tele_blocked.add(((gx, gy), (gx + dx, gy + dy)))
                                self.tele_jumps.setdefault((gx, gy), {})[dest] = idx

    def set_keys(self, keys):
        """Door specials the player can open; door sectors behind them count as open."""
        self.door_sectors = set()
        self.door_entries = {}    # door sector -> sectors it can be opened from
        for v1, v2, fl, sp, tag, s1, s2 in self.lines:
            if (sp in DOOR_SPECIALS or (sp in KEY_DOOR_SPECIALS and sp in keys)) and s2 != -1:
                self.door_sectors.add(self.sides[s2][5])
                self.door_entries.setdefault(self.sides[s2][5], set()).update(
                    self._in_front(self.V[v1], self.V[v2]))
        self.clear = {}
        self.drop = {}

    def _in_front(self, a, b):
        """Sectors in front of line a->b, near enough to use it from. (The
        front sector itself can be a sliver too thin to stand in.)"""
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy)
        nx, ny = dy / n, -dx / n
        return {self.sector_at(a[0] + dx * t + nx * d, a[1] + dy * t + ny * d)
                for t in (0.2, 0.5, 0.8) for d in (4, 20, 40)}

    def give_up_lift(self, sec):
        """Stop planning over a lift that couldn't be called from where it's needed."""
        self.lifts.pop(sec, None)
        self.clear = {}
        self.drop = {}

    def update_heights(self, heights):
        """Take current [floor, ceiling] pairs from the engine (live mode's `sectors`)."""
        for i, (f, c) in enumerate(heights):
            self.sectors[i] = (f, c) + tuple(self.sectors[i][2:])
        self.clear = {}
        self.drop = {}

    def center(self, gx, gy):
        # The half-unit offset keeps centres off map lines, which sit on whole units.
        return self.x0 + gx * CELL + CELL / 2 - 0.5, self.y0 + gy * CELL + CELL / 2 - 0.5

    def cell(self, x, y):
        return int((x - self.x0) // CELL), int((y - self.y0) // CELL)

    def sector_at(self, x, y):
        """Walk the BSP tree like R_PointInSubsector."""
        n = len(self.nodes) - 1
        while True:
            nx_, ny_, dx, dy = self.nodes[n][:4]
            right, left = self.nodes[n][12], self.nodes[n][13]
            child = right if (y - ny_) * dx < dy * (x - nx_) else left
            if child & 0x8000:
                seg = self.segs[self.ssectors[child & 0x7fff][1]]
                line = self.lines[seg[3]]
                sidedef = line[5] if seg[4] == 0 else line[6]
                return self.sides[sidedef][5]
            n = child

    def near_lines(self, x, y):
        return self.buckets.get((int(x) // self.B, int(y) // self.B), [])

    def is_solid(self, idx):
        v1, v2, fl, sp, tag, s1, s2 = self.lines[idx]
        return s2 == -1 or (fl & 1)

    def floor_ceil(self, sec):
        f, c = self.sectors[sec][0], self.sectors[sec][1]
        if sec in self.door_sectors and c - f < 56:
            c = f + 128   # doors open
        if sec in self.lifts:
            f = self.lifts[sec]["low"]   # boarded at the bottom
        return f, c

    def blocks(self, idx, floor, steps):
        """Does this line keep a player at this floor height at arm's length?

        With steps=False: walls and gaps too low to fit under. With
        steps=True: steps up of more than 24 units.
        """
        v1, v2, fl, sp, tag, s1, s2 = self.lines[idx]
        if s2 == -1 or (fl & 1):
            return not steps
        a, b = self.sides[s1][5], self.sides[s2][5]
        (fa, ca), (fb, cb) = self.floor_ceil(a), self.floor_ceil(b)
        if min(ca, cb) - max(fa, fb) < 56:
            return not steps
        if a in self.lifts or b in self.lifts:
            return False          # a lift's edges are level at one end of its ride
        return steps and max(fa, fb) - floor > 24

    def _dist(self, gx, gy, steps):
        x, y = self.center(gx, gy)
        floor = self.floor_ceil(self.cell_sector[gx, gy])[0]
        best = 999
        for idx in self.near_lines(x, y):
            if self.blocks(idx, floor, steps):
                (ax, ay), (bx, by) = self.V[self.lines[idx][0]], self.V[self.lines[idx][1]]
                best = min(best, seg_dist(x, y, ax, ay, bx, by))
        return best

    def clearance(self, gx, gy):
        """Distance to the nearest wall or ledge the player can't climb."""
        if (gx, gy) not in self.clear:
            self.clear[gx, gy] = (self._dist(gx, gy, False), self._dist(gx, gy, True))
        return min(self.clear[gx, gy])

    def drop_dist(self, gx, gy):
        """Distance to the nearest edge with a drop of more than 24 units."""
        if (gx, gy) in self.drop:
            return self.drop[gx, gy]
        x, y = self.center(gx, gy)
        floor = self.floor_ceil(self.cell_sector[gx, gy])[0]
        best = 999
        for idx in self.near_lines(x, y):
            v1, v2, fl, sp, tag, s1, s2 = self.lines[idx]
            if s2 == -1 or self.sides[s1][5] in self.lifts or self.sides[s2][5] in self.lifts:
                continue
            fa = self.floor_ceil(self.sides[s1][5])[0]
            fb = self.floor_ceil(self.sides[s2][5])[0]
            if floor - min(fa, fb) > 24:
                (ax, ay), (bx, by) = self.V[v1], self.V[v2]
                best = min(best, seg_dist(x, y, ax, ay, bx, by))
        self.drop[gx, gy] = best
        return best

    def can_step(self, a, b):
        """Can the player walk from cell a to neighbouring cell b?"""
        if b not in self.cell_sector or (a, b) in self.tele_blocked:
            return False
        self.clearance(*a)
        self.clearance(*b)
        wall_b, step_b = self.clear[b]
        if wall_b < RADIUS:
            return False
        sa, sb = self.cell_sector[a], self.cell_sector[b]
        fa, ca = self.floor_ceil(sa)
        fb, cb = self.floor_ceil(sb)
        # Below a ledge you can't get close to it, but you can land there
        # after dropping off it, and you can always back away from it.
        if step_b < RADIUS and not (fa - fb > 24 or step_b > self.clear[a][1]):
            return False
        if cb - fb < 56:
            return False
        if (self.one_way_doors and sb in self.door_sectors and sa != sb
                and sa not in self.door_entries[sb]
                and self.sectors[sb][1] - self.sectors[sb][0] < 56):
            return False          # a closed door, from a side it doesn't open from
        if sa != sb:
            if fb - fa > 24 and not (sa in self.lifts and fb - self.lifts[sa]["high"] <= 24):
                return False      # (unless riding a lift up to it)
            if min(ca, cb) - max(fa, fb) < 56:
                return False
        # No solid line between the two centres.
        x1, y1 = self.center(*a)
        x2, y2 = self.center(*b)
        for idx in set(self.near_lines(x1, y1)) | set(self.near_lines(x2, y2)):
            if self.is_solid(idx):
                (ax, ay), (bx, by) = self.V[self.lines[idx][0]], self.V[self.lines[idx][1]]
                if segs_cross(x1, y1, x2, y2, ax, ay, bx, by):
                    return False
        return True

    def nearest_ok(self, x, y):
        """The closest cell to (x, y) that a player fits in, preferring ones
        on the same floor (next to a ledge, the closest may be on top)."""
        c = self.cell(x, y)
        floor = self.floor_ceil(self.sector_at(x, y))[0]

        def other_floor(cc):
            return (cc not in self.cell_sector
                    or abs(self.floor_ceil(self.cell_sector[cc])[0] - floor) > 24)

        cands = [(other_floor(cc), abs(dx) + abs(dy), cc)
                 for dx in range(-4, 5) for dy in range(-4, 5)
                 for cc in [(c[0] + dx, c[1] + dy)]]
        for _, _, cc in sorted(cands):
            if cc in self.cell_sector and self.clearance(*cc) >= RADIUS + 2:
                f, ce = self.floor_ceil(self.cell_sector[cc])
                if ce - f >= 56:
                    return cc
        return c

    def path(self, start, goal, near=0):
        """A* from start to goal; returns the list of cells, or None.

        With near, any cell within that distance of goal will do (to pick
        up something from next to the ledge it's on, say).
        """
        s, g = self.nearest_ok(*start), self.nearest_ok(*goal)
        openq = [(0, 0, s)]
        came = {s: None}
        cost = {s: 0}
        while openq:
            _, c, cur = heapq.heappop(openq)
            if cur == g:
                break
            if near and math.hypot(self.center(*cur)[0] - goal[0],
                                   self.center(*cur)[1] - goal[1]) <= near:
                g = cur
                break
            if c > cost[cur]:
                continue
            # Steps to the eight neighbours, and jumps through teleporters.
            moves = [(nb, CELL * 2) for nb in self.tele_jumps.get(cur, ())]
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = (cur[0] + dx, cur[1] + dy)
                    if (dx or dy) and self.can_step(cur, nb):
                        moves.append((nb, math.hypot(dx, dy) * CELL))
            for nb, step in moves:
                cl = self.clearance(*nb)
                if cl < 40:
                    step *= 1 + (40 - cl) / 20   # keep off the walls
                dd = self.drop_dist(*nb)
                if dd < 40:
                    step *= 1 + (40 - dd) / 10   # and away from drop-offs
                if self.cell_sector[nb] in self.hurt:
                    step *= 25                   # and well off damaging floors
                nc = c + step
                if nc < cost.get(nb, 1e18):
                    cost[nb] = nc
                    came[nb] = cur
                    h = math.hypot(nb[0] - g[0], nb[1] - g[1]) * CELL
                    heapq.heappush(openq, (nc + h, nc, nb))
        if g not in came and near:
            # Nothing that close: the closest place reachable will do if
            # it's near enough to reach out (or bump up) to the goal from.
            best = min(came, key=lambda c: math.hypot(self.center(*c)[0] - goal[0],
                                                      self.center(*c)[1] - goal[1]))
            if math.hypot(self.center(*best)[0] - goal[0], self.center(*best)[1] - goal[1]) <= near + 32:
                g = best
        if g not in came:
            return None
        cells = []
        cur = g
        while cur is not None:
            cells.append(cur)
            cur = came[cur]
        return cells[::-1]

    def walkable_line(self, a, b, on_path=()):
        """Does a straight walk from cell a to cell b stay on steppable cells?
        Off the cells of the planned path (on_path), it must also keep off
        damaging floors and well away from drops, which a running player
        can drift over."""
        (x1, y1), (x2, y2) = self.center(*a), self.center(*b)
        n = max(1, int(math.hypot(x2 - x1, y2 - y1) / (CELL / 2)))
        prev = a
        for k in range(1, n + 1):
            c = self.cell(x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n)
            if c != prev:
                if not self.can_step(prev, c) or self.clearance(*c) < RADIUS + 6:
                    return False
                if c not in on_path and (self.cell_sector[c] in self.hurt
                                         or self.drop_dist(*c) < 40):
                    return False
                prev = c
        return True

    def in_reach(self, p, q):
        """Is there no wall (or a step too high, or too little headroom)
        on the straight line from p to q?"""
        for idx in set(self.near_lines(*p)) | set(self.near_lines(*q)):
            v1, v2, fl, sp, tag, s1, s2 = self.lines[idx]
            (ax, ay), (bx, by) = self.V[v1], self.V[v2]
            if not segs_cross(p[0], p[1], q[0], q[1], ax, ay, bx, by):
                continue
            if s2 == -1 or fl & 1:
                return False
            (fa, ca), (fb, cb) = self.floor_ceil(self.sides[s1][5]), self.floor_ceil(self.sides[s2][5])
            if min(ca, cb) - max(fa, fb) < 56 or abs(fa - fb) > 24:
                return False
        return True

    def near_drop(self, p, q, within=28):
        """Does the straight line from p to q pass that close to a drop?"""
        n = max(1, int(math.hypot(q[0] - p[0], q[1] - p[1]) / 8))
        for k in range(n + 1):
            c = self.cell(p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n)
            if c in self.cell_sector and self.drop_dist(*c) < within:
                return True
        return False

    def safe_walk(self, p, q):
        """Would a straight run from p toward q keep off drops, damaging
        floors and teleporters? Following the map lines it crosses: a wall
        or a step too high stops the player, which is fine."""
        n = math.hypot(q[0] - p[0], q[1] - p[1])
        if n < 1:
            return True
        near = set()
        for k in range(int(n // 64) + 2):
            f = min(1, k * 64 / n)
            near.update(self.near_lines(p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f))
        crossings = []
        for idx in near:
            v1, v2, fl, sp, tag, s1, s2 = self.lines[idx]
            (ax, ay), (bx, by) = self.V[v1], self.V[v2]
            if segs_cross(p[0], p[1], q[0], q[1], ax, ay, bx, by):
                den = (p[0] - q[0]) * (ay - by) - (p[1] - q[1]) * (ax - bx)
                t = ((p[0] - ax) * (ay - by) - (p[1] - ay) * (ax - bx)) / den if den else 0
                crossings.append((t, idx))
        start = self.sector_at(*p)
        floor = self.sectors[start][0]
        for t, idx in sorted(crossings):
            v1, v2, fl, sp, tag, s1, s2 = self.lines[idx]
            if s2 == -1 or fl & 1:
                return True                        # a wall
            f = t + 2 / n
            after = self.sector_at(p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f)
            fa, ca = self.sectors[after][0], self.sectors[after][1]
            if ca - fa < 56 or fa - floor > 24:
                return True                        # too low to get under, or a step up
            (ax, ay), (bx, by) = self.V[v1], self.V[v2]
            if sp in TELEPORT_SPECIALS and (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) < 0:
                return False                       # a teleporter, from the front
            if fa < self.sectors[start][0] - 24 or (after in self.hurt and start not in self.hurt):
                return False                       # a drop, or a damaging floor
            floor = fa
        return True

    def waypoints(self, cells):
        """Shorten a cell path into straight legs, as map coordinates."""
        on_path = set(cells)
        out = [cells[0]]
        i = 0
        while i < len(cells) - 1:
            j = len(cells) - 1
            while j > i + 1 and not self.walkable_line(cells[i], cells[j], on_path):
                j -= 1
            out.append(cells[j])
            i = j
        return [self.center(*c) for c in out]


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0 if length2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def segs_cross(x1, y1, x2, y2, x3, y3, x4, y4):
    def orient(ax, ay, bx, by, cx, cy):
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    d1 = orient(x3, y3, x4, y4, x1, y1)
    d2 = orient(x3, y3, x4, y4, x2, y2)
    d3 = orient(x1, y1, x2, y2, x3, y3)
    d4 = orient(x1, y1, x2, y2, x4, y4)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)
