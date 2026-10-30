"""Small raster helpers on numpy: morphology, labelling, contour tracing, simplification."""

from __future__ import annotations

import numpy as np


def _shift_or(m, r):
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy > r * r:
                continue
            out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def dilate(m, r):
    return _shift_or(m, r)


def erode(m, r):
    return ~_shift_or(~m, r)


def close(m, r):
    return erode(dilate(m, r), r)


def fill_segment(img, p, q, width):
    """Set the cells of a thick segment p-q (cell units)."""
    H, W = img.shape
    p, q = np.asarray(p, float), np.asarray(q, float)
    lo = np.floor(np.minimum(p, q) - width).astype(int)
    hi = np.ceil(np.maximum(p, q) + width).astype(int)
    x0, y0 = max(lo[0], 0), max(lo[1], 0)
    x1, y1 = min(hi[0], W - 1), min(hi[1], H - 1)
    if x1 < x0 or y1 < y0:
        return
    ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
    cx, cy = xs + 0.5, ys + 0.5
    d = q - p
    L2 = float(d @ d) or 1e-9
    t = np.clip(((cx - p[0]) * d[0] + (cy - p[1]) * d[1]) / L2, 0, 1)
    dist = np.hypot(cx - (p[0] + t * d[0]), cy - (p[1] + t * d[1]))
    img[y0:y1 + 1, x0:x1 + 1] |= dist <= width / 2


def label(m):
    """4-connected components by row runs and union-find. Returns (labels, count)."""
    H, W = m.shape
    parent = [0]

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    labels = np.zeros((H, W), np.int32)
    prev = []
    for y in range(H):
        row = m[y]
        d = np.diff(np.concatenate([[0], row.view(np.int8), [0]]))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        cur = []
        j = 0
        for s, e in zip(starts, ends):
            lab = None
            while j < len(prev) and prev[j][1] <= s:
                j += 1
            k = j
            while k < len(prev) and prev[k][0] < e:
                pl = find(prev[k][2])
                if lab is None:
                    lab = pl
                elif pl != lab:
                    parent[max(pl, lab)] = min(pl, lab)
                    lab = min(pl, lab)
                k += 1
            if lab is None:
                lab = len(parent)
                parent.append(lab)
            cur.append((s, e, lab))
            labels[y, s:e] = lab
        prev = cur
    roots = np.array([find(i) for i in range(len(parent))])
    uniq, compact = np.unique(roots, return_inverse=True)
    compact = compact.astype(np.int32)  # root 0 is the background and maps to 0
    return compact[labels], len(uniq) - 1


def trace(mask):
    """Outer boundary of the largest blob in mask, as cell-corner points (x, y), counter-clockwise."""
    H, W = mask.shape
    m = np.zeros((H + 2, W + 2), bool)
    m[1:-1, 1:-1] = mask
    ys, xs = np.nonzero(m)
    # start at the lowest row, leftmost cell: its lower-left corner is on the outer boundary
    i = np.lexsort((xs, ys))[0]
    sx, sy = int(xs[i]), int(ys[i])
    # walk the cell corners keeping the inside on the left (counter-clockwise in y-up)
    x, y, dx, dy = sx, sy, 1, 0
    pts = [(x, y)]

    def cell(cx, cy):
        return m[cy, cx]

    for _ in range(4 * (H + 2) * (W + 2)):
        # cells ahead-left and ahead-right of the corner (x, y) heading (dx, dy)
        if (dx, dy) == (1, 0):
            left, right = cell(x, y), cell(x, y - 1)
        elif (dx, dy) == (0, 1):
            left, right = cell(x - 1, y), cell(x, y)
        elif (dx, dy) == (-1, 0):
            left, right = cell(x - 1, y - 1), cell(x - 1, y)
        else:
            left, right = cell(x, y - 1), cell(x - 1, y - 1)
        if right:
            dx, dy = dy, -dx      # turn right
        elif not left:
            dx, dy = -dy, dx      # turn left
        x, y = x + dx, y + dy
        if pts[-1] != (x, y):
            pts.append((x, y))
        if (x, y) == (sx, sy):
            break
    pts.pop()
    return [(px - 1, py - 1) for px, py in pts]


def simplify(poly, tol):
    """Douglas-Peucker on a closed polygon; drops collinear points."""
    if len(poly) < 4:
        return poly
    p = np.array(poly, float)
    # split at the two farthest points
    i0 = 0
    i1 = int(np.argmax(np.hypot(*(p - p[0]).T)))

    def dp(a):
        keep = np.zeros(len(a), bool)
        keep[0] = True
        stack = [(0, len(a) - 1)]
        while stack:
            i, j = stack.pop()
            if j - i < 2:
                continue
            s, e = a[i], a[j]
            d = e - s
            L = np.hypot(*d)
            seg = a[i + 1:j]
            if L < 1e-12:
                dist = np.hypot(*(seg - s).T)
            else:
                dist = np.abs(d[0] * (seg[:, 1] - s[1]) - d[1] * (seg[:, 0] - s[0])) / L
            k = int(np.argmax(dist))
            if dist[k] > tol:
                keep[i + 1 + k] = True
                stack += [(i, i + 1 + k), (i + 1 + k, j)]
        return list(a[keep])

    first = np.vstack([p[i0:i1 + 1]])
    second = np.vstack([p[i1:], p[:1]])
    out = dp(first) + dp(second)
    return [(float(a), float(b)) for a, b in out]
