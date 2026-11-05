"""deslab: make the room outlines and the walls of a scene agree (Игорь 04.11.2026: the measured
plan's outline dimensions must be exact; run JK showed a room outline with an 8 mm slant and
wall ends 46-195 mm off the room's steps). Works on the scene dicts, after the analysis."""


def _xy(p):
    return (float(p["x"]), float(p["y"]))


def _square_outline(poly, tol=0.012):
    """An edge off its axis by less than tol on one side becomes exactly square (its far end
    takes the near end's coordinate); points on a straight line go."""
    pts = [list(p) for p in poly]
    n = len(pts)
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        dx, dy = abs(q[0] - p[0]), abs(q[1] - p[1])
        if dy < tol <= dx:
            q[1] = p[1]
        elif dx < tol <= dy:
            q[0] = p[0]
    out = []
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        if abs(a[0] - b[0]) < 1e-9 and abs(b[0] - c[0]) < 1e-9:
            continue
        if abs(a[1] - b[1]) < 1e-9 and abs(b[1] - c[1]) < 1e-9:
            continue
        if abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9:
            continue
        out.append((b[0], b[1]))
    return out


def _walls_on(walls, a, b, tol=0.006):
    """Axis-aligned walls whose face lies on the axis-aligned segment a-b, with their axis index
    (0 for a wall along x) and whether start or end is nearer to a and to b."""
    horizontal = abs(a[1] - b[1]) < 1e-9
    lo, hi = sorted((a[0], b[0]) if horizontal else (a[1], b[1]))
    line = a[1] if horizontal else a[0]
    found = []
    for w in walls:
        s, e = _xy(w["start"]), _xy(w["end"])
        if horizontal and abs(s[1] - e[1]) > 1e-6 or not horizontal and abs(s[0] - e[0]) > 1e-6:
            continue
        axis = s[1] if horizontal else s[0]
        half = float(w.get("thickness", 0.2)) / 2
        if min(abs(axis + half - line), abs(axis - half - line)) > tol:
            continue
        ws, we = sorted((s[0], e[0]) if horizontal else (s[1], e[1]))
        if min(we, hi) - max(ws, lo) < 0.5 * (hi - lo) - 0.25:
            continue
        found.append(w)
    return found


def _fill_notches(poly, walls, size=0.2):
    """A small two-step notch of the outline that no wall face carries (a scan lump in a
    corner, JK: 141 x 115 mm at the end of W5) is filled: the two faces around it run on to
    their corner. Returns the outline and the notches filled."""
    filled = []
    changed = True
    while changed and len(poly) > 4:
        changed = False
        n = len(poly)
        for i in range(n):
            p0, p1, p2, p3, p4 = (poly[(i + k) % n] for k in range(5))
            short = all(max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= size for a, b in ((p1, p2), (p2, p3)))
            if not short or _walls_on(walls, p1, p2) or _walls_on(walls, p2, p3):
                continue
            if not (_walls_on(walls, p0, p1) and _walls_on(walls, p3, p4)):
                continue
            horizontal = abs(p0[1] - p1[1]) < 1e-9
            corner = (p3[0], p1[1]) if horizontal else (p1[0], p3[1])
            poly = [q for k, q in enumerate(poly) if k not in ((i + 1) % n, (i + 2) % n, (i + 3) % n)]
            poly.insert((i + 1) % n if (i + 1) % n <= len(poly) else len(poly), corner)
            filled.append((p2, abs(p1[0] - p3[0]), abs(p1[1] - p3[1])))
            changed = True
            break
    return _square_outline(poly), filled


def fit_walls_to_rooms(scene, reach=0.25):
    """Squares each room outline and moves wall ends onto the room's steps: where the outline
    steps between two parallel faces, the walls carrying those faces end exactly at the step
    (within reach). Returns notes of what moved."""
    notes = []
    walls = scene.get("walls", [])
    for room in scene.get("rooms", []):
        poly = _square_outline([_xy(p) for p in room["polygon"]])
        room["polygon"] = [{"x": round(x, 4), "y": round(y, 4)} for x, y in poly]
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            prev, nxt = poly[i - 1], poly[(i + 2) % n]
            step_x = abs(a[0] - b[0]) < 1e-9          # a vertical step between two horizontal edges
            length = abs(a[1] - b[1]) if step_x else abs(a[0] - b[0])
            if length > 1.0:
                continue
            pos = a[0] if step_x else a[1]
            for edge, other_edge in (((prev, a), (b, nxt)), ((b, nxt), (prev, a))):
                takeover = bool(_walls_on(walls, *other_edge))
                for w in _walls_on(walls, *edge):
                    s, e = _xy(w["start"]), _xy(w["end"])
                    k = 0 if step_x else 1
                    for key, p in (("start", s), ("end", e)):
                        if 1e-4 < abs(p[k] - pos) <= reach:
                            # only the end on the step's side of the wall's face edge
                            other = e if key == "start" else s
                            edge_far = edge[0] if abs(edge[1][k] - pos) < 1e-9 else edge[1]
                            if (edge_far[k] - pos) * (other[k] - pos) < 0:
                                continue
                            # an end short of the step comes out to it; one past the step goes
                            # back only when another wall carries the face beyond (a wall that
                            # runs on outside the room, to a T joint, stays)
                            past = (p[k] - pos) * (edge_far[k] - pos) < 0
                            if past and not takeover:
                                continue
                            notes.append(f"{w['id']}: {key} {p[k]:.3f} → {pos:.3f} (ступень контура {room.get('id', '')})")
                            w[key]["x" if k == 0 else "y"] = round(pos, 4)
    for room in scene.get("rooms", []):
        poly, filled = _fill_notches([_xy(p) for p in room["polygon"]], walls)
        if filled:
            room["polygon"] = [{"x": round(x, 4), "y": round(y, 4)} for x, y in poly]
            for (x, y), dx, dy in filled:
                notes.append(f"{room.get('id', '')}: выступ {dx * 1000:.0f} x {dy * 1000:.0f} мм у ({x:.3f}; {y:.3f}) "
                             "без стены убран из контура")
    return notes
