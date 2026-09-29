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
KEY_DOOR_SPECIALS = {26, 27, 28, 32, 33, 34}


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
    def __init__(self, wad_path, mapname, keys=()):
        lumps = read_map(wad_path, mapname)
        self.V = lumps["vertexes"]
        self.lines = lumps["linedefs"]
        self.sides = lumps["sidedefs"]
        self.sectors = lumps["sectors"]
        self.things = lumps["things"]
        self.nodes = lumps["nodes"]
        self.ssectors = lumps["ssectors"]
        self.segs = lumps["segs"]

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

    def set_keys(self, keys):
        """Door specials the player can open; door sectors behind them count as open."""
        self.door_sectors = set()
        for v1, v2, fl, sp, tag, s1, s2 in self.lines:
            if (sp in DOOR_SPECIALS or (sp in KEY_DOOR_SPECIALS and sp in keys)) and s2 != -1:
                self.door_sectors.add(self.sides[s2][5])
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
            if s2 == -1:
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
        if b not in self.cell_sector:
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
        if sa != sb:
            if fb - fa > 24:
                return False
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
        """The closest cell to (x, y) that a player fits in."""
        c = self.cell(x, y)
        cands = [(abs(dx) + abs(dy), (c[0] + dx, c[1] + dy))
                 for dx in range(-4, 5) for dy in range(-4, 5)]
        for _, cc in sorted(cands):
            if cc in self.cell_sector and self.clearance(*cc) >= RADIUS + 2:
                f, ce = self.floor_ceil(self.cell_sector[cc])
                if ce - f >= 56:
                    return cc
        return c

    def path(self, start, goal):
        """A* from start to goal; returns the list of cells, or None."""
        s, g = self.nearest_ok(*start), self.nearest_ok(*goal)
        openq = [(0, 0, s)]
        came = {s: None}
        cost = {s: 0}
        while openq:
            _, c, cur = heapq.heappop(openq)
            if cur == g:
                break
            if c > cost[cur]:
                continue
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == dy == 0:
                        continue
                    nb = (cur[0] + dx, cur[1] + dy)
                    if not self.can_step(cur, nb):
                        continue
                    step = math.hypot(dx, dy) * CELL
                    cl = self.clearance(*nb)
                    if cl < 40:
                        step *= 1 + (40 - cl) / 20   # keep off the walls
                    dd = self.drop_dist(*nb)
                    if dd < 40:
                        step *= 1 + (40 - dd) / 10   # and away from drop-offs
                    nc = c + step
                    if nc < cost.get(nb, 1e18):
                        cost[nb] = nc
                        came[nb] = cur
                        h = math.hypot(nb[0] - g[0], nb[1] - g[1]) * CELL
                        heapq.heappush(openq, (nc + h, nc, nb))
        if g not in came:
            return None
        cells = []
        cur = g
        while cur is not None:
            cells.append(cur)
            cur = came[cur]
        return cells[::-1]

    def walkable_line(self, a, b):
        """Does a straight walk from cell a to cell b stay on steppable cells?"""
        (x1, y1), (x2, y2) = self.center(*a), self.center(*b)
        n = max(1, int(math.hypot(x2 - x1, y2 - y1) / (CELL / 2)))
        prev = a
        for k in range(1, n + 1):
            c = self.cell(x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n)
            if c != prev:
                if not self.can_step(prev, c) or self.clearance(*c) < RADIUS + 6:
                    return False
                prev = c
        return True

    def waypoints(self, cells):
        """Shorten a cell path into straight legs, as map coordinates."""
        out = [cells[0]]
        i = 0
        while i < len(cells) - 1:
            j = len(cells) - 1
            while j > i + 1 and not self.walkable_line(cells[i], cells[j]):
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
