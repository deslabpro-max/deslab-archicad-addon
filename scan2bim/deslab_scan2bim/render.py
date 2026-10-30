"""Plan preview of a scene (needs Pillow): walls grey, openings red/blue, rooms outlined."""

from __future__ import annotations

import math

from PIL import Image, ImageDraw


def render(scene, path, scale=100, margin=40):
    def P(p):
        return (p["x"], p["y"])

    pts = [P(p) for w in scene["walls"] for p in (w["start"], w["end"])]
    pts += [P(p) for r in scene["rooms"] for p in r["polygon"]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0 = min(xs) - 0.5, min(ys) - 0.5
    W = int((max(xs) - x0 + 0.5) * scale) + 2 * margin
    H = int((max(ys) - y0 + 0.5) * scale) + 2 * margin
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    def T(p):
        return (margin + (p[0] - x0) * scale, H - margin - (p[1] - y0) * scale)

    for r in scene["rooms"]:
        poly = [P(p) for p in r["polygon"]]
        d.polygon([T(p) for p in poly], outline=(0, 150, 0), fill=(235, 250, 235))
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        d.text(T((cx, cy)), f"{r['number']}  {r['area']} m2  H{int(r['height']*1000)}  f{int(r['floor']*1000)}", fill=(0, 100, 0))
    for w in scene["walls"]:
        (ax, ay), (bx, by) = P(w["start"]), P(w["end"])
        L = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / L, (by - ay) / L
        nx, ny = -uy, ux
        t = w["thickness"] / 2
        quad = [(ax + nx * t, ay + ny * t), (bx + nx * t, by + ny * t), (bx - nx * t, by - ny * t), (ax - nx * t, ay - ny * t)]
        d.polygon([T(p) for p in quad], fill=(150, 150, 150) if w["measuredThickness"] else (200, 170, 120), outline="black")
        d.text(T(((ax + bx) / 2 + nx * 0.3, (ay + by) / 2 + ny * 0.3)), f"{w['id']} {int(w['thickness']*1000)}", fill="black")
        for o in w["openings"]:
            c0 = o["centerOffset"] - o["width"] / 2
            c1 = o["centerOffset"] + o["width"] / 2
            p0 = (ax + ux * c0, ay + uy * c0)
            p1 = (ax + ux * c1, ay + uy * c1)
            col = (220, 0, 0) if o["kind"] == "door" else (0, 0, 220)
            d.line([T(p0), T(p1)], fill=col, width=6)
            d.text(T(((p0[0] + p1[0]) / 2 - nx * 0.35, (p0[1] + p1[1]) / 2 - ny * 0.35)),
                   f"{int(o['width']*1000)} s{int(o['sill']*1000)} h{int(o['height']*1000)}", fill=col)
    img.save(path)
