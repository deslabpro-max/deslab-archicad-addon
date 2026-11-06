"""deslab: layout check of a built plan without looking at a picture (Игорь 06.11.2026: each fix cost
too many tokens and too much time). It redraws the texts and lines the way Archicad draws them,
by sizes measured on kv94 (06.11.2026), and lists what touches: a line through a dimension text,
two texts on each other, a label outside the flat. One plan at a time (the measured plan and the
engineering plan are separate views)."""
import math

from .obmer import _inside, _seg_box

K = 0.045
DIGIT = 1.95 * K      # a digit of a dimension text («1 201»: 0.414 m of ink)
GAP = 1.18 * K        # the thousands gap
TH = 0.145            # dimension text height, 0.03 m over its line
LAB_CH = 1.80 * K     # a label letter («Вытяжка» 13.7 mm, «Стояк канализации» 30.0 mm)
LAB_PAD = 1.0 * K
LAB_H = 3.45 * K


def _p(p):
    return (float(p["x"]), float(p["y"]))


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _mul(a, k):
    return (a[0] * k, a[1] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _left(u):
    return (-u[1], u[0])


def _rect(cs):
    xs = [c[0] for c in cs]
    ys = [c[1] for c in cs]
    return (min(xs), min(ys), max(xs), max(ys))


def _hits(a, b, m=0.005):
    return a[0] < b[2] - m and b[0] < a[2] - m and a[1] < b[3] - m and b[1] < a[3] - m


def _label_box(lb):
    m, e = _p(lb["midCoordinate"]), _p(lb["endCoordinate"])
    txt = "".join(r["text"] for r in lb["runs"])
    parts = [x for x in txt.split("\n") if x.strip()] or [txt]
    w = max(len(x) for x in parts) * LAB_CH + LAB_PAD
    x0 = e[0] if e[0] >= m[0] else e[0] - w
    if len(parts) == 1:
        return (x0, m[1], x0 + w, m[1] + LAB_H), parts[0]
    return (x0, m[1] - 0.16, x0 + w, m[1] + 0.40), " / ".join(parts)


def geometry(chains, labels):
    texts, lines = [], []
    for ci, ch in enumerate(chains):
        d, ref = _p(ch["direction"]), _p(ch["referencePoint"])
        read = (1.0, 0.0) if abs(d[0]) >= abs(d[1]) else (0.0, 1.0)
        side = _left(read)
        pts = [_p(p) for p in ch["points"]]
        ts = [_dot(_sub(p, ref), d) for p in pts]
        owner = ("размер", ch.get("edge"))
        lines.append((owner, _add(ref, _mul(d, min(ts) - 0.05)), _add(ref, _mul(d, max(ts) + 0.05)), "размерная линия"))
        for p, t in zip(pts, ts):
            q = _add(ref, _mul(d, t))
            v = _sub(q, p)
            n = math.hypot(*v)
            lines.append((owner, p, _add(q, _mul(v, 0.05 / n)) if n > 1e-9 else q, "выносная линия"))
        tl, ta = ch.get("textLeaders", {}), ch.get("textAt", {})
        for k in range(len(ts) - 1):
            mm = int(round(abs(ts[k + 1] - ts[k]) * 1000))
            s = f"{mm:,}".replace(",", " ")
            w = len(str(mm)) * DIGIT + (GAP if mm >= 1000 else 0)
            j = k + 1
            if j in tl:
                b, m, e = _p(tl[j]["beg"]), _p(tl[j]["mid"]), _p(tl[j]["end"])
                lines += [(owner, b, m, "выноска размера"), (owner, m, e, "выноска размера")]
                if abs(e[0] - m[0]) > abs(e[1] - m[1]):
                    u = (1.0 if e[0] >= m[0] else -1.0, 0.0)
                else:
                    u = (0.0, 1.0 if e[1] >= m[1] else -1.0)
                box = _rect([_add(e, _mul(side, 0.01)), _add(_add(e, _mul(u, w)), _mul(side, 0.01 + TH))])
            else:
                c = _p(ta[j]) if j in ta else _add(ref, _mul(d, (ts[k] + ts[k + 1]) / 2))
                a0, a1 = _add(c, _mul(read, -w / 2)), _add(c, _mul(read, w / 2))
                box = _rect([_add(a0, _mul(side, 0.03)), _add(a1, _mul(side, 0.03 + TH))])
            texts.append((s, owner, box, "размер"))
    for li, lb in enumerate(labels):
        b, m, e = _p(lb["begCoordinate"]), _p(lb["midCoordinate"]), _p(lb["endCoordinate"])
        box, text = _label_box(lb)
        owner = ("выноска", li)
        lines += [(owner, b, m, "выноска"), (owner, m, e, "выноска"), (owner, (box[0], m[1]), (box[2], m[1]), "полка")]
        texts.append((text[:30], owner, box, "подпись"))
    return texts, lines


def check(scene, chains, labels):
    """Short Russian lines, one per collision."""
    texts, lines = geometry(chains, labels)
    rooms = [[_p(p) for p in r["polygon"]] for r in scene.get("rooms", [])]
    out = []
    for name, owner, box, kind in texts:
        core = (box[0] + 0.005, box[1] + 0.005, box[2] - 0.005, box[3] - 0.005)
        hit = sorted({f"{lk} {o[1]}" for o, a, b, lk in lines if o != owner and _seg_box(a, b, core)})
        if hit:
            out.append(f"{kind} «{name}» перечёркнут: {', '.join(hit[:3])}")
        if kind == "подпись":
            corners = ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3]))
            if rooms and not all(any(_inside(r, c) for r in rooms) for c in corners):
                out.append(f"подпись «{name}» вне квартиры")
    for i, (n1, o1, b1, k1) in enumerate(texts):
        for n2, o2, b2, k2 in texts[i + 1:]:
            if _hits(b1, b2):
                out.append(f"«{n1}» налезает на «{n2}»")
    return sorted(set(out))
