"""deslab: the AI review step of the measured plan (Игорь 06.11.2026: «все должно обрабатываться через ИИ»).

The scan helper writes scene.json, the model looks at the scene (and pictures, when there are some) and answers ONLY with
a JSON review: openings (kind, sashes, swing), engineering items to add, clutter to drop, room names and wet zones, doubts
for Игорь. apply_review() applies the review where it passes hard checks and lists what it rejected, so the model decides
WHAT to build and the code keeps the drawing rules.

Stdlib only. Provider: Polza.ai, OpenAI-compatible chat completions; the key is read from POLZA_API_KEY (the process
environment, on Windows also the user scope, because setx does not reach running processes). The key is never printed,
logged or put into an error text."""
import base64
import copy
import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULTS = {
    "baseUrl": "https://api.polza.ai/api/v1",
    "model": "anthropic/claude-sonnet-5.5",
    "hardModel": "anthropic/claude-opus-5.5",
    "timeout": 180,
    "retries": 1,
    "maxTokens": 16000,
    "rulesFile": "правила-черчения.md",
}


class AIError(RuntimeError):
    pass


def settings(profile=None):
    out = dict(DEFAULTS)
    out.update((profile or {}).get("ai", {}))
    if os.environ.get("POLZA_MAX_TOKENS"):  # deslab: reasoning models spend the whole 16000 on thinking (DeepSeek V4.1 Flash)
        out["maxTokens"] = int(os.environ["POLZA_MAX_TOKENS"])
    if os.environ.get("POLZA_MODEL"):  # deslab: Игорь 06.11.2026, cheaper models (Kimi, DeepSeek) for tests without editing the profile
        out["model"] = os.environ["POLZA_MODEL"]
    return out


def api_key():
    key = os.environ.get("POLZA_API_KEY")
    if not key and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                key = winreg.QueryValueEx(k, "POLZA_API_KEY")[0]
        except OSError:
            key = None
    if not key:
        raise AIError("Нет ключа: переменная POLZA_API_KEY не задана ни в процессе, ни в пользовательских переменных Windows.")
    return key.strip()


def _clean(text, key):
    return str(text).replace(key, "***") if key else str(text)


def _request(method, url, key, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data, method=method, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "deslab-bridge"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def list_models(profile=None):
    cfg = settings(profile)
    key = api_key()
    try:
        data = _request("GET", cfg["baseUrl"].rstrip("/") + "/models", key, timeout=60)
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise AIError("Список моделей: " + _clean(error, key)) from None
    return data.get("data", data)


def image_part(path):
    """An image as a base64 data URL part of a chat message."""
    raw = Path(path).read_bytes()
    kind = "png" if raw[:4] == b"\x89PNG" else "jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:image/{kind};base64," + base64.b64encode(raw).decode("ascii")}}


def pdf_page_png(pdf, page, out, width=2000):
    """A PDF page (1-based) rendered to a PNG at most `width` px wide (needs the extra «ai»: pymupdf)."""
    try:
        import pymupdf
    except ImportError:
        raise AIError("Нужен PyMuPDF: pip install pymupdf (extra «ai»).") from None
    doc = pymupdf.open(str(pdf))
    pg = doc[int(page) - 1]
    zoom = width / pg.rect.width
    pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(str(out))
    return str(out)


# deslab: Игорь 08.11.2026 (the Polza balance ran out): the model may also be Claude Code on this PC, under Игорь's own
# subscription. Model "claude-code" (or "claude-code:opus", "claude-code:sonnet") runs `claude -p` headless; pictures are
# written to a temp folder and read by Claude Code itself. No key is involved.
CLAUDE_CODE = "claude-code"


def _claude_executable():
    import shutil
    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        if found:
            return found
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or "~").expanduser()
    for candidate in (home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude",
                      home / "AppData" / "Roaming" / "npm" / "claude.cmd"):
        if candidate.exists():
            return str(candidate)
    raise AIError("Не найден Claude Code (команда claude) на этом ПК.")


def _chat_claude_code(messages, profile, model, json_mode):
    import subprocess
    import tempfile
    cfg = settings(profile)
    variant = model.split(":", 1)[1] if ":" in model else "sonnet"
    # deslab: on Windows the finished claude process can still hold the folder; never lose the answer over cleanup.
    with tempfile.TemporaryDirectory(prefix="deslab-ai-", ignore_cleanup_errors=True) as folder:
        parts, pictures = [], 0
        for message in messages:
            content = message["content"]
            items = content if isinstance(content, list) else [{"type": "text", "text": content}]
            block = []
            for item in items:
                if item.get("type") == "text":
                    block.append(item["text"])
                elif item.get("type") == "image_url":
                    url = item["image_url"]["url"]
                    head, data = url.split(",", 1)
                    pictures += 1
                    path = Path(folder) / f"picture{pictures}.{'png' if 'png' in head else 'jpg'}"
                    path.write_bytes(base64.b64decode(data))
                    block.append(f"[Картинка: {path} — открой её инструментом Read.]")
            title = {"system": "ИНСТРУКЦИЯ", "user": "ЗАПРОС", "assistant": "ТВОЙ ПРОШЛЫЙ ОТВЕТ"}.get(message["role"], message["role"])
            parts.append(f"===== {title} =====\n" + "\n".join(block))
        if json_mode:
            parts.append("Ответь ТОЛЬКО одним JSON-объектом, без пояснений и без ```.")
        prompt = "\n\n".join(parts)
        command = [_claude_executable(), "-p", "--output-format", "json", "--model", variant,
                   "--allowedTools", "Read", "--add-dir", folder]
        try:
            done = subprocess.run(command, input=prompt.encode("utf-8"), capture_output=True, cwd=tempfile.gettempdir(),
                                  timeout=max(int(cfg["timeout"]), 600))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AIError("Claude Code не ответил: " + str(error)) from None
        out = done.stdout.decode("utf-8", "replace")
        try:
            data = json.loads(out)
        except ValueError:
            raise AIError("Claude Code: " + (out or done.stderr.decode("utf-8", "replace"))[:400]) from None
        if data.get("is_error"):
            raise AIError("Claude Code: " + str(data.get("result"))[:400])
        # deslab: which model really answered (11.11.2026: the report said only «claude-code», a Fable run could not be told from Sonnet)
        usage = dict(data.get("usage") or {}, cost=data.get("total_cost_usd"), provider=CLAUDE_CODE,
                     models=sorted(data.get("modelUsage") or {}), requested=variant)
        return data.get("result", ""), usage


def chat(messages, profile=None, model=None, json_mode=True):
    """One chat-completions call with a timeout and one retry. Returns (text, usage)."""
    cfg = settings(profile)
    if (model or cfg["model"]).startswith(CLAUDE_CODE):
        return _chat_claude_code(messages, profile, model or cfg["model"], json_mode)
    key = api_key()
    body = {"model": model or cfg["model"], "messages": messages, "max_tokens": cfg["maxTokens"], "temperature": 0.1}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    last = None
    for attempt in range(int(cfg["retries"]) + 1):
        try:
            data = _request("POST", cfg["baseUrl"].rstrip("/") + "/chat/completions", key, body, timeout=cfg["timeout"])
            return data["choices"][0]["message"]["content"], data.get("usage", {})
        except urllib.error.HTTPError as error:
            last = f"HTTP {error.code}: " + _clean(error.read().decode("utf-8", "replace")[:300], key)
            if error.code < 500 and error.code != 429:
                break
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            last = _clean(error, key)
        time.sleep(2 + 3 * attempt)
    raise AIError("Запрос к модели не удался: " + str(last))


# ---------------------------------------------------------------- the review

OPENING_KINDS = ("window", "door", "balconyDoor", "opening")  # deslab: "opening" is a doorway without a leaf
MEP_KINDS = ("shaft", "sewerRiser", "waterRiser", "heatingRiser", "radiator", "panel")
# deslab: 13.11.2026, what the model may call a numbered candidate of the scan helper (scene "candidates")
CANDIDATE_KINDS = ("radiator", "heatingRiser", "waterRiser", "sewerRiser", "shaft", "box", "duct", "panel", "vent")
CANDIDATE_SKIP = ("clutter", "furniture", "windowBoard", "other", "none")
WET_KINDS = ("bathroom", "kitchen")

REVIEW_SCHEMA = {
    "openings": [{"id": "W1.1", "kind": "window|door|balconyDoor|opening (a doorway without a leaf)", "sashes": "1..4 (window)",
                  "swing": {"hinge": "start|end (the jamb with the hinge: the start or the end of the wall segment, as the wall runs start to end)",
                            "opens": "into|out (into the flat's room or outward, to the stairwell / outside)"},
                  "confidence": "0..1", "note": "short text"}],
    "newOpenings": [{"wallId": "W1", "id": "W1.9", "kind": "window|door|balconyDoor", "centerOffset": "m along the wall",
                     "width": "m", "sill": "m", "height": "m", "confidence": "0..1"}],
    "mep": [{"kind": "shaft|sewerRiser|waterRiser|radiator|panel", "id": "SH1", "label": "text",
             "polygon": "[{x,y}] for a shaft", "at": "{x,y} for a riser or panel", "diameter": "m for a riser",
             "start": "{x,y}", "end": "{x,y}", "depth": "m for a radiator", "confidence": "0..1"}],
    "_panelNote": "a panel (щиток) may be given as a point `at` on the wall: the code makes the segment",
    "candidates": [{"id": "C1 (an id from the scene's `candidates`, every one must be answered)",
                    "kind": "radiator|heatingRiser|waterRiser|sewerRiser|shaft|box|duct|panel|vent|clutter|furniture|windowBoard|other",
                    "confidence": "0..1 (0.4 and more is drawn; the candidate itself is real, you only doubt its name)",
                    "parts": "2 only for a candidate with a `seam` that the pictures show as two things side by side there", "note": "short text"}],
    "pipes": [{"id": "P1 (an id from the scene's `pipes`, every one must be answered)",
               "system": "heating|water|sewer|vent|clutter", "confidence": "0..1", "note": "short text"}],
    "vents": [{"wallId": "W4", "side": "left|right (the face, as in the picture's title)", "along": "m along the wall, from the picture's axis",
               "height": "m over the floor", "confidence": "0..1"}],
    "clutter": [{"wallId": "W7", "reason": "short text"}],
    "rooms": [{"id": "R1", "name": "text"}],
    "wetZones": [{"kind": "bathroom|kitchen", "polygon": "[{x,y}]"}],
    "doubts": ["question for Игорь"],
}

MEMORY_RULES = """Правила офиса, которые нельзя нарушать (кратко):
- Стены бетонные или газобетонные, углы 90 градусов, если в исходнике нет явного другого угла.
- Окна ПВХ белые, открываются внутрь помещения; одна створка, если ширина меньше 0.9 м; подоконников на обмерном плане нет.
- Входная дверь: сторону открывания берём с исходника (PDF или скан), ошибка критична; без исходника ставь сомнение в doubts, а не угадывай.
- Подписи не лежат поверх размеров и стен; на чертеже только то, что есть на исходнике.
- Мелкие предметы на полу (плитка, мусор) не стены: такие «стены» нужно предлагать в clutter.
- Коммуникации: вентшахты, стояки канализации и воды, радиаторы и щиток рисуем и размеряем; трассировка это низкая стена.
- Обозначения на плане дизайнера: вентшахта это контур с внутренним проёмом у стены; рядом со стояком канализации (круг около 110-160 мм) внутри шахты стоят
  маленькие кружки 30-50 мм: это стояки воды (ХВС/ГВС), их нужно выдать как waterRiser (kind "waterRiser", at, diameter около 0.05), каждый кружок отдельным элементом.
  Радиатор это тонкий прямоугольник вдоль стены (start, end, depth около 0.07), щиток (panel) прямоугольник у стены.
- Открывание двери: не придумывай флаги Archicad, а скажи по картинке, у какого косяка петля (hinge: start или end, считая вдоль стены от её начала к концу в сцене)
  и куда открывается (opens: into или out). Перевод в флаги делает код. У балконной двери тоже укажи swing: hinge по развёртке её стены
  (ручка стоит напротив петель), opens почти всегда into.
- Картинки облака точек: «plan-low/mid/high» это срезы плана на разной высоте, «wall-Wn-left/right» это развёртка грани стены
  (по горизонтали метры вдоль стены от её начала, по вертикали высота над полом; цвет показывает, насколько предмет выступает из стены).
  Рамки с номерами C1, C2… это кандидаты, которые алгоритм нашёл и ИЗМЕРИЛ в облаке (в сцене раздел candidates: стена, грань, from/to
  вдоль стены, bottom/top, depth). Размеры кандидатов не меняй и не придумывай свои: только назови каждый номер в разделе candidates.
  radiator: панель под окном или у стены, низ 0.05-0.3 м, верх до 1.2 м. heatingRiser: вертикальная труба 15-40 мм от пола до потолка
  рядом с радиатором или окном, обычно пара. sewerRiser: труба около 110 мм, обычно в санузле или шахте. waterRiser: трубы 25-50 мм
  рядом с канализационным стояком (ХВС и ГВС, обычно две). shaft: выступающий короб вентиляции на всю высоту с решёткой или отверстием.
  box: короб без решётки вокруг стояков (гипсокартон): в нём или вплотную к нему стоят трубы P из раздела pipes.
  duct: открытый прямоугольный вентиляционный воздуховод от пола до потолка (металл или пластик, поперечные стыки-фланцы):
  ширина 0.1-0.6 м, глубина 0.15-0.35 м, ровные грани, обычно в санузле, на кухне или у шахты; два-три рядом разной ширины
  это отдельные кандидаты (или один с seam), назови каждый duct. Кандидат во всю высоту такой формы, в котором нет стояка P, это duct, а не box. panel: электрощит. vent: решётка или отверстие вентиляции.
  hint кандидата это форма по облаку: «riser?» выступает из стены примерно на свою ширину (круглая труба), «duct or box?» прямоугольный
  во всю высоту. heatingRiser, waterRiser, sewerRiser давай только кандидату, который по форме может быть трубой: прямоугольный код не построит.
  seam у кандидата: по облаку там слабый стык (seamStrength в м, seamDepths глубины двух сторон). Если на снимках кандидат в этом месте
  это две вещи рядом (два воздуховода, воздуховод и короб), добавь parts: 2, код разрежет его по seam, kind относится к обеим частям.
  face кандидата: грань по высоте, измеренная в полосах по 10 см. cover это доля полос, где предмет закрывает больше 60 % своей ширины,
  flat это разброс выступа полос в метрах. У воздуховода, короба и шахты cover около 1 и flat до 0.02; если cover меньше 0.6 или flat
  больше 0.04 (hint «wires or clutter?»), это не duct, не box и не shaft (код такое не построит), а провода, мусор или мебель: clutter.
  Старая электропроводка и её остатки: свисающие со стены или потолка провода и пучки, гофра, распаечные коробки, маленький старый щиток
  или коробка 20-40 см, из которой висят провода: clutter, на чертёж не идут. panel это только квартирный электрощит: ровный ящик
  с дверцей, без висящих вокруг проводов.
  Мебель, шторы, подоконники, откосы, строительный мусор: clutter, furniture, windowBoard или other, на чертёж они не идут.
- Трубы P1, P2… (раздел pipes сцены) алгоритм нашёл и измерил в облаке сам: kind riser это стояк (ось x, y, от z до z), run это труба
  вдоль стены (два конца оси, высота z), diameter в метрах, joins показывает, к какой трубе она присоединена, hint это догадка алгоритма.
  Назови систему каждой трубы в разделе pipes: heating (пара тонких стояков у окна и трубы от них к радиатору), water (тонкие стояки и
  отводы в санузле или кухне), sewer (около 110 мм, отводы 50 мм у пола), vent (круглый воздуховод 100-160 мм под потолком или стояк).
  Не труба (косяк, наличник, кабель-канал, провод, кромка радиатора, карниз, плинтус, мебель): clutter. Код не построит трубу тоньше
  16 мм вдали от санузла, кухни, радиаторов и стояков отопления (это провод), отопление без радиатора или стояка отопления рядом и
  воду или канализацию вдали от санузла, кухни и их стояков: такие трубы в прихожей или комнате обычно провода, обувь, сумки или
  мебель, называй их clutter. Поэтому обязательно назови санузел и кухню в rooms, а кухонную зону в комнате дай в wetZones. Труба с joins почти всегда той же
  системы, что и труба, к которой она присоединена. Геометрию труб не меняй. Кандидата C…, который стоит на месте стояков P…
  (группа труб, принятая за короб), называй clutter: трубы будут построены сами.
- Вентиляционные отверстия и решётки в стене, у которых нет номера кандидата, выдай в vents: стена, грань, расстояние вдоль стены и высота
  по осям развёртки.
- Проёмы: если на развёртке в дверном проёме нет полотна и коробки (пустой проём в бетоне), kind = "opening"; входная дверь с полотном это door.
- Маленькое помещение со стояками канализации и воды это санузел: назови его в rooms."""


def build_system_prompt(profile=None, rules_text=None):
    cfg = settings(profile)
    if rules_text is None:
        for base in (Path.cwd(), Path(__file__).resolve().parents[3], Path.home() / "Desktop" / "Архикад"):
            path = base / cfg["rulesFile"]
            if path.exists():
                rules_text = path.read_text(encoding="utf-8")
                break
    return ("Ты помощник архитектора-обмерщика. Тебе дают сцену обмера (метры, ось Y вверх) и иногда картинки. "
            "Проверь сцену и предложи ПРАВКИ. Ответь ТОЛЬКО одним JSON-объектом без пояснений и без Markdown, по схеме:\n"
            + json.dumps(REVIEW_SCHEMA, ensure_ascii=False, indent=1)
            + "\nНе выдумывай то, чего нет в данных: если не уверен, положи вопрос в doubts, а confidence поставь ниже 0.6. "
            "Все координаты в тех же метрах и осях, что и в сцене. Пустые разделы оставляй пустыми списками.\n\n"
            + MEMORY_RULES + ("\n\nПолные правила черчения офиса:\n" + rules_text if rules_text else ""))


def scene_for_model(scene, with_mep=True):
    """The scene without the noise: what the model needs to judge walls, openings and rooms."""
    names = ("source", "levels", "walls", "rooms", "slabs", "assumptions", "warnings", "candidates", "pipes") + (("mep", "tracing") if with_mep else ())
    return json.dumps({k: scene[k] for k in names if k in scene}, ensure_ascii=False, separators=(",", ":"))


def parse_json(text):
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise AIError("Модель вернула не JSON.")
    try:
        return json.loads(text[start:end + 1])
    except ValueError as error:
        raise AIError("Модель вернула неверный JSON: " + str(error)) from None


def ai_review(scene, images=(), profile=None, hard=False, call=chat, with_mep=True, model=None):
    """Asks the model for a review. `call` is replaceable for tests. Returns (review, usage, raw_text).
    TODO next step: pictures straight from the point cloud (top slices, wall elevations); for now `images` is a list of
    file paths (a plan PNG of the helper, a PDF page rendered to PNG)."""
    cfg = settings(profile)
    content = [{"type": "text", "text": "Сцена обмера (JSON):\n" + scene_for_model(scene, with_mep)}]
    content += [image_part(p) for p in images]
    messages = [{"role": "system", "content": build_system_prompt(profile)}, {"role": "user", "content": content}]
    text, usage = call(messages, profile, model or (cfg["hardModel"] if hard else cfg["model"]))
    usage = dict(usage or {}, model=model or (cfg["hardModel"] if hard else cfg["model"]))
    try:
        return parse_json(text), usage, text
    except AIError as error:
        error.raw, error.usage = text, usage  # deslab: keep the answer, it was paid for
        raise


# --------------------------------------------------------- applying, with checks

def _point_in_polygon(p, polygon):
    x, y, inside = p["x"], p["y"], False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a["y"] > y) != (b["y"] > y) and x < (b["x"] - a["x"]) * (y - a["y"]) / (b["y"] - a["y"]) + a["x"]:
            inside = not inside
    return inside


def _flat_polygons(scene):
    return [s["polygon"] for s in scene.get("slabs", []) if s.get("polygon")] or [r["polygon"] for r in scene.get("rooms", []) if r.get("polygon")]


def _by_polygon(p, polygon, margin):
    """The point lies in the polygon or at most `margin` from its outline."""
    pts = [(float(q["x"]), float(q["y"])) for q in polygon]
    if _point_in_polygon({"x": p[0], "y": p[1]}, polygon):
        return True
    for a, b in zip(pts, pts[1:] + pts[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        k = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1.0)))
        if math.hypot(p[0] - a[0] - k * dx, p[1] - a[1] - k * dy) <= margin:
            return True
    return False


def _inside_flat(p, scene, tolerance=0.3):
    polys = _flat_polygons(scene)
    if not polys:
        return True
    return any(_point_in_polygon({"x": p["x"] + dx, "y": p["y"] + dy}, poly)
               for poly in polys for dx in (-tolerance, 0, tolerance) for dy in (-tolerance, 0, tolerance))


def _near_polygon(p, polygon, tolerance=0.4):
    """deslab: kv94 has risers that stand just outside the shaft contour (the contour is drawn around the box), so
    «inside» means inside or within 0.4 m."""
    return any(_point_in_polygon({"x": p["x"] + dx, "y": p["y"] + dy}, polygon)
               for dx in (-tolerance, 0, tolerance) for dy in (-tolerance, 0, tolerance))


def _panel_on_wall(scene, item):
    """deslab: the builder wants a panel as a segment ON a wall face (start, end, depth), like the radiators. The model gives a
    point, so the code finds the nearest wall, drops the point on its face on the side where the point lies and makes a 0.32 m
    segment (the size of kv94's panel)."""
    p = item["at"]
    best = None
    for w in scene.get("walls", []):
        ax, ay, bx, by = w["start"]["x"], w["start"]["y"], w["end"]["x"], w["end"]["y"]
        length = math.hypot(bx - ax, by - ay)
        if not length:
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        along = (p["x"] - ax) * ux + (p["y"] - ay) * uy
        across = -(p["x"] - ax) * uy + (p["y"] - ay) * ux
        if 0 <= along <= length and (best is None or abs(across) < abs(best[0])):
            best = (across, along, ax, ay, ux, uy, w.get("thickness", 0.2))
    if best is None or abs(best[0]) > 0.6:
        return None
    across, along, ax, ay, ux, uy, thickness = best
    side = 1 if across > 0 else -1
    nx, ny = -uy * side, ux * side
    fx, fy = ax + ux * along + nx * thickness / 2, ay + uy * along + ny * thickness / 2
    half = 0.16
    out = {k: v for k, v in item.items() if k != "at"}
    out.update(start={"x": round(fx - ux * half, 3), "y": round(fy - uy * half, 3)}, end={"x": round(fx + ux * half, 3), "y": round(fy + uy * half, 3)})
    return out


def _is_point(p):
    return isinstance(p, dict) and all(isinstance(p.get(k), (int, float)) and math.isfinite(p[k]) for k in ("x", "y"))


def _wall_length(w):
    return math.hypot(w["end"]["x"] - w["start"]["x"], w["end"]["y"] - w["start"]["y"])


def swing_flags(scene, wall, swing, at=None):
    """deslab: the model says WHERE the hinge is and which way the door opens; the code turns that into Archicad's
    `reflected` (hinge at the wall's end instead of its start) and `oSide` (opens to the left of the wall direction instead of
    the right). Calibrated on the one door Игорь confirmed on kv94: W3.1, wall running south (start y=10.07, end y=-0.1),
    the flat on its left, the door opens OUT (to the right) with the hinge at the wall's END = reflected True, oSide False.
    Returns (reflected, oSide) or None when the flat's side of the wall cannot be told."""
    if not isinstance(swing, dict) or swing.get("hinge") not in ("start", "end") or swing.get("opens") not in ("into", "out"):
        return None
    dx, dy = wall["end"]["x"] - wall["start"]["x"], wall["end"]["y"] - wall["start"]["y"]
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    mx, my = (wall["start"]["x"] + wall["end"]["x"]) / 2, (wall["start"]["y"] + wall["end"]["y"]) / 2
    left = {"x": mx - dy / length * 0.5, "y": my + dx / length * 0.5}
    right = {"x": mx + dy / length * 0.5, "y": my - dx / length * 0.5}
    polys = _flat_polygons(scene)
    in_left, in_right = (any(_point_in_polygon(p, poly) for poly in polys) for p in (left, right))
    if in_left == in_right:
        # deslab: 13.11.2026, Nagornaya: the slab runs to the OUTER outline of the walls and the outer wall is 0.64 m thick,
        # so both points 0.5 m off the axis were «in the flat» and the balcony door's swing was refused. The rooms tell the
        # side: a point just past each face of the wall, at the opening when it is known
        reach = float(wall.get("thickness", 0.2)) / 2 + 0.25
        if at is not None:
            mx, my = wall["start"]["x"] + dx / length * at, wall["start"]["y"] + dy / length * at
        left = {"x": mx - dy / length * reach, "y": my + dx / length * reach}
        right = {"x": mx + dy / length * reach, "y": my - dx / length * reach}
        rooms = [r["polygon"] for r in scene.get("rooms", []) if r.get("polygon")]
        in_left, in_right = (any(_point_in_polygon(p, poly) for poly in rooms) for p in (left, right))
    if in_left == in_right:
        return None
    flat_on_left = in_left
    opens_left = flat_on_left if swing["opens"] == "into" else not flat_on_left
    return swing["hinge"] == "end", bool(opens_left)


def _confident(item, minimum):
    try:
        return float(item.get("confidence", 1)) >= minimum
    except (TypeError, ValueError):
        return False


# deslab: 13.11.2026, the systems a pipe of the scan helper may carry, with the label of its riser
PIPE_SYSTEMS = {"heating": "Стояк отопления", "water": "Стояк воды", "sewer": "Стояк канализации", "vent": "Вентиляция"}

OFFICE_LABELS = {"shaft": "Вытяжка", "radiator": None, "panel": "Щиток", "sewerRiser": "Стояк канализации", "waterRiser": "Стояк воды",
                 "heatingRiser": "Стояк отопления", "box": "Короб", "vent": "Вент. отв"}


def _face_point(wall, side, along):
    """The point on a wall face: `side` "left"/"right" of the wall direction, `along` metres from the wall's start."""
    dx, dy = wall["end"]["x"] - wall["start"]["x"], wall["end"]["y"] - wall["start"]["y"]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    k = (1 if side == "left" else -1) * float(wall.get("thickness", 0.2)) / 2
    return {"x": round(wall["start"]["x"] + ux * along - uy * k, 4), "y": round(wall["start"]["y"] + uy * along + ux * k, 4)}


def split_candidate(c, least=0.05):
    """deslab: 13.11.2026, a candidate the model sees as two things side by side (Nagornaya: a narrow and a wide vent duct)
    is cut at the seam the helper measured; each half keeps the depth of its side. None without a seam inside it."""
    seam, t0, t1 = c.get("seam"), float(c["from"]), float(c["to"])
    if seam is None or not t0 + least <= float(seam) <= t1 - least:
        return None
    seam = float(seam)
    ux, uy = (c["end"]["x"] - c["start"]["x"]) / (t1 - t0), (c["end"]["y"] - c["start"]["y"]) / (t1 - t0)
    mx, my = (c["start"]["x"] + c["end"]["x"]) / 2, (c["start"]["y"] + c["end"]["y"]) / 2
    nx, ny = c["at"]["x"] - mx, c["at"]["y"] - my
    norm = math.hypot(nx, ny) or 1.0
    nx, ny = nx / norm, ny / norm
    depths = c.get("seamDepths") or [c["depth"], c["depth"]]
    out = []
    for (a, b), depth, tag in (((t0, seam), depths[0], "a"), ((seam, t1), depths[1], "b")):
        start = {"x": round(c["start"]["x"] + ux * (a - t0), 4), "y": round(c["start"]["y"] + uy * (a - t0), 4)}
        end = {"x": round(c["start"]["x"] + ux * (b - t0), 4), "y": round(c["start"]["y"] + uy * (b - t0), 4)}
        mid = ((start["x"] + end["x"]) / 2, (start["y"] + end["y"]) / 2)
        out.append({**{k: v for k, v in c.items() if k not in ("seam", "seamStrength", "seamDepths")},
                    "id": c["id"] + tag, "from": round(a, 3), "to": round(b, 3), "depth": round(float(depth), 3),
                    "start": start, "end": end,
                    "at": {"x": round(mid[0] + nx * float(depth) / 2, 4), "y": round(mid[1] + ny * float(depth) / 2, 4)}})
    return out


def _round_shape(c):
    """deslab: 14.11.2026, a candidate that can be a pipe at a wall: it stands out of the wall by about its width (a clamp
    gap of up to 10 cm more), the same rule as the helper's «riser?» hint."""
    width, depth = float(c["to"]) - float(c["from"]), float(c["depth"])
    return width - 0.04 <= depth <= width + 0.1


# deslab: 14.11.2026, Nagornaya hall: old wiring hanging down by the entrance door was built as a duct 260x150 and its
# 12 mm wires as heating pipes (Игорь: «маленький щиток, и остатки старой проводки висят… такие вещи нужно, чтобы ты
# распознавал»). A duct or a box has a face at every height (the helper's `face`: cover, the share of 10 cm bands it
# fills over 60 % of its width; flat, the spread of the bands' fronts); a pipe thinner than a heating pipe that stands
# neither in a wet room nor by a radiator or a heating riser is a cable.
FACE_COVER = 0.6
FACE_FLAT = 0.04
CABLE_DIAMETER = 0.016
WET_WORDS = ("сануз", "с/у", "ванн", "туалет", "уборн", "кухн", "постироч", "bath", "kitchen", "wc")


def _without_face(c):
    face = c.get("face")
    return isinstance(face, dict) and (float(face.get("cover", 1)) < FACE_COVER or float(face.get("flat", 0)) > FACE_FLAT)


def _wet_polygons(scene, review):
    """Rooms named as wet ones (by the model's names, or the scene's) and the model's wet zones."""
    names = {r["id"]: r.get("name", "") for r in scene.get("rooms", [])}
    for item in review.get("rooms", []) or []:
        if item.get("id") in names and isinstance(item.get("name"), str):
            names[item["id"]] = item["name"]
    polys = [r["polygon"] for r in scene.get("rooms", [])
             if r.get("polygon") and any(w in str(names.get(r["id"], "")).lower() for w in WET_WORDS)]
    polys += [z["polygon"] for z in review.get("wetZones", []) or []
              if isinstance(z.get("polygon"), list) and len(z["polygon"]) >= 3 and all(_is_point(q) for q in z["polygon"])]
    return polys


def _to_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    k = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1.0)))
    return math.hypot(p[0] - a[0] - k * dx, p[1] - a[1] - k * dy)


def _misplaced(p, wet, radiators, heating, wet_risers, near_radiator=1.0, near_riser=0.6, near_wet=1.0):
    """deslab: 14.11.2026, why a pipe of the helper cannot be what the model named it, or None. A pipe thinner than 16 mm away
    from wet rooms, radiators and heating risers is a cable; a heating pipe needs a radiator or a heating riser by it, a
    water or a sewer pipe a wet room or a water or sewer riser (Nagornaya hall: 50 mm «pipes» at 0.33 and 1.66 m under the old
    panel). A riser of 16 mm and more is a riser wherever it stands."""
    system, d = p.get("system"), float(p.get("diameter", 0))
    ends = [(float(q["x"]), float(q["y"])) for q in p.get("path", [])]
    if not ends or system not in ("heating", "water", "sewer"):
        return None
    in_wet = any(_by_polygon(e, poly, 0.15) for e in ends for poly in wet) or any(
        math.hypot(e[0] - r["path"][0]["x"], e[1] - r["path"][0]["y"]) <= near_wet for e in ends for r in wet_risers if r is not p)
    by_radiator = any(_to_segment(e, (r["start"]["x"], r["start"]["y"]), (r["end"]["x"], r["end"]["y"])) <= near_radiator
                      for e in ends for r in radiators)
    by_heating = any(math.hypot(e[0] - h["path"][0]["x"], e[1] - h["path"][0]["y"]) <= near_riser
                     for e in ends for h in heating if h is not p)
    if d < CABLE_DIAMETER and system in ("heating", "water") and not (in_wet or by_radiator or by_heating):
        return "кабель"
    if p.get("kind") == "riser" and d >= CABLE_DIAMETER:
        return None
    if system == "heating" and not (by_radiator or by_heating):
        return "отопление без радиатора и стояка рядом"
    if system in ("water", "sewer") and not in_wet:
        return "вода или канализация вдали от санузла, кухни и стояков"
    return None


def candidate_item(c, kind, number):
    """deslab: 13.11.2026, a numbered candidate of the scan helper named by the model becomes an engineering item. Every
    number is the cloud's (the helper measured the candidate), the model gives the name only."""
    mx, my = (c["start"]["x"] + c["end"]["x"]) / 2, (c["start"]["y"] + c["end"]["y"]) / 2
    nx, ny = c["at"]["x"] - mx, c["at"]["y"] - my
    norm = math.hypot(nx, ny) or 1.0
    nx, ny = nx / norm, ny / norm
    width, depth = float(c["to"]) - float(c["from"]), float(c["depth"])
    tall = float(c["top"]) - float(c["bottom"])
    if kind in ("heatingRiser", "waterRiser", "sewerRiser"):
        lo, hi = (0.09, 0.16) if kind == "sewerRiser" else (0.02, 0.06)
        d = min(max(min(width, depth), lo), hi)
        off = max(depth - d / 2, d / 2)
        return {"kind": kind, "id": f"{'K' if kind == 'sewerRiser' else 'T'}{number}", "candidate": c["id"],
                "at": {"x": round(mx + nx * off, 4), "y": round(my + ny * off, 4)}, "diameter": round(d, 3),
                "label": OFFICE_LABELS[kind]}
    if kind == "radiator":
        return {"kind": kind, "id": f"RD{number}", "candidate": c["id"], "start": c["start"], "end": c["end"],
                "depth": round(min(depth, 0.2), 3), "height": round(tall, 3), "elevation": round(float(c["bottom"]), 3)}
    if kind == "panel":
        return {"kind": kind, "id": f"EP{number}", "candidate": c["id"], "start": c["start"], "end": c["end"],
                "depth": round(depth, 3), "height": round(tall, 3), "elevation": round(float(c["bottom"]), 3), "label": OFFICE_LABELS[kind]}
    if kind == "vent":
        return {"kind": kind, "id": f"V{number}", "candidate": c["id"], "at": {"x": round(mx, 4), "y": round(my, 4)},
                "height": round((float(c["top"]) + float(c["bottom"])) / 2, 3), "label": OFFICE_LABELS[kind]}
    # a shaft or a box: the rectangle standing out of the wall
    a, b = c["start"], c["end"]
    polygon = [dict(a), dict(b), {"x": round(b["x"] + nx * depth, 4), "y": round(b["y"] + ny * depth, 4)},
               {"x": round(a["x"] + nx * depth, 4), "y": round(a["y"] + ny * depth, 4)}]
    if kind == "duct":
        # deslab: 13.11.2026, a rectangular vent duct is a solid box (a morph), laid out like a shaft (dimensions, label)
        return {"kind": "shaft", "duct": True, "id": f"VD{number}", "candidate": c["id"], "polygon": polygon,
                "elevation": round(float(c["bottom"]), 3) if float(c["bottom"]) > 0.15 else 0.0, "height": round(tall, 3),
                "label": f"Воздуховод {int(round(width * 1000))}×{int(round(depth * 1000))}"}
    # deslab: Nagornaya run 3, a box 0.14 m wide with walls of 100 mm blocks gave a wall of zero length: a narrow box has
    # thinner walls (a third of its smaller size)
    return {"kind": "shaft", "id": f"{'SH' if kind == 'shaft' else 'BX'}{number}", "candidate": c["id"], "polygon": polygon,
            "thickness": round(min(0.1, min(width, depth) / 3), 3), "label": OFFICE_LABELS[kind]}


def _carries_outline(scene, wall):
    """deslab: True when a room outline edge lies on this wall: without it the outline check finds more faults."""
    from . import obmer
    without = dict(scene, walls=[w for w in scene.get("walls", []) if w is not wall])
    return len(obmer.check_room_faces(without)) > len(obmer.check_room_faces(scene))


def apply_review(scene, review, min_confidence=0.5, shaft_confidence=0.3, candidate_confidence=0.4):
    """Applies the review to a COPY of the scene. Returns (new_scene, report) with report = {"applied": [...],
    "rejected": [...], "doubts": [...]}. Rules: an opening stays on its wall and does not overlap another; points are inside
    the flat; a riser is inside a shaft; only short or tracing walls may be dropped; low confidence is not applied.
    deslab: Nagornaya run 1, a candidate is a thing the helper measured in the cloud, the model only names it, so its bar is
    lower (0.4: the risers at 0.4-0.45 were lost and the engineering sheet stayed half empty)."""
    new = copy.deepcopy(scene)
    applied, rejected, doubts = [], [], []
    walls = {w["id"]: w for w in new.get("walls", [])}
    openings = {o["id"]: (w, o) for w in new.get("walls", []) for o in w.get("openings", [])}

    for item in review.get("openings", []) or []:
        oid = item.get("id")
        if oid not in openings:
            rejected.append(f"проём {oid}: нет такого проёма в сцене")
            continue
        if not _confident(item, min_confidence):
            rejected.append(f"проём {oid}: уверенность {item.get('confidence')} ниже порога")
            continue
        wall, opening = openings[oid]
        if item.get("kind") in OPENING_KINDS and item["kind"] != opening.get("kind"):
            if {item["kind"], opening.get("kind")} <= {"door", "balconyDoor", "opening"}:
                opening["kind"] = item["kind"]
            else:
                rejected.append(f"проём {oid}: смена типа {opening.get('kind')} на {item['kind']} не принимается")
        sashes = item.get("sashes")
        if sashes is not None:
            if isinstance(sashes, int) and 1 <= sashes <= 4 and opening.get("kind") == "window" and not (opening["width"] < 0.9 and sashes > 1):
                opening["sashes"] = sashes
            else:
                rejected.append(f"проём {oid}: створок {sashes} не принимается (окно уже 0.9 м только с одной створкой)")
        if "swing" in item and opening.get("kind") == "balconyDoor":
            # deslab: the balcony door is a window: the bridge needs the jamb with the hinge only
            if isinstance(item["swing"], dict) and item["swing"].get("hinge") in ("start", "end"):
                opening["hinge"] = item["swing"]["hinge"]
        elif "swing" in item and opening.get("kind") not in ("window", "opening"):
            flags = swing_flags(new, wall, item["swing"], opening.get("centerOffset"))
            if flags is None:
                rejected.append(f"проём {oid}: открывание {item['swing']} не переводится (неизвестна сторона квартиры или неверные значения)")
            else:
                opening["reflected"], opening["oSide"] = flags
        for k in ("reflected", "oSide"):
            if k in item and isinstance(item[k], bool) and opening.get("kind") != "window" and "swing" not in item:
                opening[k] = item[k]
        applied.append(f"проём {oid}: " + ", ".join(f"{k}={opening[k]}" for k in ("kind", "sashes", "reflected", "oSide") if k in opening))

    for item in review.get("newOpenings", []) or []:
        wall = walls.get(item.get("wallId"))
        try:
            offset, width = float(item["centerOffset"]), float(item["width"])
        except (KeyError, TypeError, ValueError):
            rejected.append(f"новый проём {item.get('id')}: нет centerOffset или width")
            continue
        if not wall or item.get("kind") not in OPENING_KINDS:
            rejected.append(f"новый проём {item.get('id')}: нет стены {item.get('wallId')} или неверный тип")
        elif not _confident(item, min_confidence):
            rejected.append(f"новый проём {item.get('id')}: низкая уверенность")
        elif offset - width / 2 < 0.05 or offset + width / 2 > _wall_length(wall) - 0.05:
            rejected.append(f"новый проём {item.get('id')}: выходит за стену {wall['id']}")
        elif any(abs(offset - o["centerOffset"]) < (width + o["width"]) / 2 for o in wall.get("openings", [])):
            rejected.append(f"новый проём {item.get('id')}: пересекается с другим проёмом")
        else:
            wall.setdefault("openings", []).append({k: item[k] for k in ("id", "kind", "width", "sill", "height", "centerOffset") if k in item})
            applied.append(f"новый проём {item.get('id')} на {wall['id']}")

    mep = list(new.get("mep", []))
    shafts = [m for m in mep if m.get("kind") == "shaft"]
    ids = {m.get("id") for m in mep}
    for item in review.get("mep", []) or []:
        kind, mid = item.get("kind"), item.get("id")
        if kind not in MEP_KINDS or not mid or mid in ids:
            rejected.append(f"инженерный элемент {mid}: неверный тип или повтор id")
            continue
        if new.get("candidates") and kind != "panel":
            # deslab: Nagornaya run 4, next to the measured candidates the model added radiators with sizes of its own: with a
            # cloud the numbers come from the cloud only
            rejected.append(f"инженерный элемент {mid}: размеры не из облака (есть кандидаты), не строю")
            continue
        # deslab: risers need not stand in a shaft (kv94 K3 is 2.5 m from any), so the only hard check is «inside the flat»;
        # near a shaft (0.8 m) the confidence bar is lower, because the circles there are small and easy to read wrongly
        in_shaft = kind in ("sewerRiser", "waterRiser", "heatingRiser") and _is_point(item.get("at")) and any(
            s.get("polygon") and _near_polygon(item["at"], s["polygon"], 0.8) for s in shafts)
        if not _confident(item, shaft_confidence if in_shaft else min_confidence):  # deslab: risers inside a shaft need less certainty
            rejected.append(f"инженерный элемент {mid}: низкая уверенность")
            continue
        if kind == "panel" and "at" in item and "start" not in item:
            item = _panel_on_wall(new, item)
            if item is None:
                rejected.append(f"щиток {mid}: рядом нет стены")
                continue
        pts = [item.get("at")] if "at" in item else item.get("polygon", []) if kind == "shaft" else [item.get("start"), item.get("end")]
        if not pts or not all(_is_point(p) for p in pts) or not all(_inside_flat(p, new) for p in pts):
            rejected.append(f"инженерный элемент {mid}: координаты не в квартире")
            continue
        ids.add(mid)
        item = dict(item)
        item.pop("confidence", None)
        # deslab: the office's own short labels win over the model's wording (Игорь 06.11.2026 chose
        # «Щиток», the AI build wrote «Щиток электрический» and it was crossed by the chains)
        if kind in OFFICE_LABELS:
            if OFFICE_LABELS[kind]:
                item["label"] = OFFICE_LABELS[kind]
            else:
                item.pop("label", None)  # radiators: no label, their sizes are the dimension chain
        if kind == "shaft":
            item.setdefault("thickness", 0.1)
        elif kind in ("sewerRiser", "waterRiser", "heatingRiser"):
            item.setdefault("diameter", 0.11 if kind == "sewerRiser" else 0.05 if kind == "waterRiser" else 0.03)
        elif kind == "radiator":
            item.setdefault("depth", 0.07)
        elif kind == "panel":
            item.setdefault("depth", 0.158)
            item.setdefault("height", 0.4)
            item.setdefault("elevation", 1.6)
        # deslab: the labels of kv94's own drawing are short and few: «Вытяжка», «Стояк канализации», «Щиток»; radiators and water risers
        # have none (the model's «Радиатор 800», «шахта из пазогребневых блоков (левая стена)» ran over the dimensions)
        item["label"] = {"shaft": "Вытяжка", "sewerRiser": "Стояк канализации", "panel": "Щиток", "heatingRiser": "Стояк отопления"}.get(kind)
        if item["label"] is None:
            item.pop("label")
        mep.append(item)
        if kind == "shaft":
            shafts.append(item)
        applied.append(f"{kind} {mid}")
    # deslab: 13.11.2026 (Игорь: «инженерные узлы пустым… надо проверить все коммуникации»): the helper's numbered candidates
    # named by the model; the geometry is the cloud's
    candidates = {c["id"]: c for c in new.get("candidates", [])}
    answered = set()
    for item in review.get("candidates", []) or []:
        cid, kind = item.get("id"), item.get("kind")
        c = candidates.get(cid)
        if c is None or cid in answered:
            rejected.append(f"кандидат {cid}: нет такого номера или повтор")
            continue
        answered.add(cid)
        c["review"] = {k: item[k] for k in ("kind", "confidence", "note") if k in item}
        if kind in CANDIDATE_SKIP:
            continue
        if kind not in CANDIDATE_KINDS:
            rejected.append(f"кандидат {cid}: неизвестный тип {kind}")
        elif not _confident(item, candidate_confidence):
            rejected.append(f"кандидат {cid} ({kind}): уверенность {item.get('confidence')} ниже порога")
        elif kind in ("shaft", "box") and min(float(c["to"]) - float(c["from"]), float(c["depth"])) < 0.1:
            rejected.append(f"кандидат {cid} ({kind}): уже 0.1 м, это не короб")
        elif (kind == "panel" or kind in ("shaft", "box", "duct") and float(c["top"]) - float(c["bottom"]) >= 1.5) and _without_face(c):
            # deslab: 14.11.2026, Nagornaya hall: the old wiring by the entrance door was named a duct, the old panel it hangs
            # from could be named a panel; a duct over risers (no face where they stand apart) is said nothing about: it is them
            face = c["face"]
            rejected.append(f"кандидат {cid} ({kind}): нет ровной грани (cover {face.get('cover')}, flat {face.get('flat')}), "
                            "это провода или мусор")
            outline = candidate_item(c, "box", "")["polygon"]
            if not any(p.get("kind") == "riser" and _by_polygon((p["path"][0]["x"], p["path"][0]["y"]), outline, 0.0)
                       for p in new.get("pipes", [])):
                doubts.append(f"Кандидат {cid} модель назвала {kind}, а по облаку у него нет ровной грани: "
                              "похоже на висящие провода или мусор. Не построен.")
        elif kind in ("heatingRiser", "waterRiser", "sewerRiser") and new.get("pipes") is not None \
                and float(c["bottom"]) > 0.25 and float(c["top"]) < float(new.get("levels", {}).get("ceiling", 99)) - 0.25 \
                and not any(p.get("kind") == "riser" and math.hypot(p["path"][0]["x"] - c["at"]["x"], p["path"][0]["y"] - c["at"]["y"]) <= 0.15
                            for p in new["pipes"]):
            # deslab: 14.11.2026, Nagornaya v18: a strip 8 cm wide by the window, 0.38-3.02 m, became a third heating riser
            # of 53 mm; a riser runs from the floor to the ceiling, and the helper's pipe search found nothing round there
            rejected.append(f"кандидат {cid} ({kind}): не доходит ни до пола, ни до потолка ({c['bottom']}–{c['top']} м), "
                            "круглой трубы там поиск не нашёл, это не стояк")
        elif kind in ("heatingRiser", "waterRiser", "sewerRiser") and not _round_shape(c):
            # deslab: 14.11.2026, Nagornaya: the narrow vent duct (16 cm wide, 30 cm out of the wall) named a sewer riser
            # became a pipe of 160 mm; a pipe stands out of the wall by its own size and a clamp's gap
            width, depth = float(c["to"]) - float(c["from"]), float(c["depth"])
            rejected.append(f"кандидат {cid} ({kind}): {int(round(width * 1000))}×{int(round(depth * 1000))} мм, прямоугольный, это не труба")
            doubts.append(f"Кандидат {cid} ({int(round(width * 1000))}×{int(round(depth * 1000))} мм) модель назвала трубой, "
                          "а по облаку он прямоугольный: воздуховод или короб? Не построен.")
        else:
            halves = split_candidate(c) if str(item.get("parts")) == "2" else None
            if str(item.get("parts")) == "2" and halves is None:
                rejected.append(f"кандидат {cid}: parts 2 без стыка seam, построен целым")
            for part, number in (zip(halves, ("a", "b")) if halves else ((c, ""),)):
                made = candidate_item(part, kind, cid[1:] + number)
                if made["id"] in ids:
                    rejected.append(f"кандидат {cid}: повтор id {made['id']}")
                    continue
                ids.add(made["id"])
                mep.append(made)
                applied.append(f"{kind} {made['id']} из кандидата {cid}")
    for cid in candidates:
        if cid not in answered:
            rejected.append(f"кандидат {cid}: модель не ответила")
    # deslab: 13.11.2026 (Игорь: «у тебя почему-то короб… трубы должны повторяться по диаметрам и направлению, как в облаке»):
    # the helper's pipes named by the model; a pipe keeps the cloud's axis and diameter, a riser gets the office label
    pipes = {p["id"]: p for p in new.get("pipes", [])}
    named = set()
    for item in review.get("pipes", []) or []:
        pid, system = item.get("id"), item.get("system")
        p = pipes.get(pid)
        if p is None or pid in named:
            rejected.append(f"труба {pid}: нет такого номера или повтор")
            continue
        named.add(pid)
        p["review"] = {k: item[k] for k in ("system", "confidence", "note") if k in item}
        if system in ("clutter", "none", "other"):
            continue
        if system not in PIPE_SYSTEMS:
            rejected.append(f"труба {pid}: неизвестная система {system}")
        elif not _confident(item, candidate_confidence):
            rejected.append(f"труба {pid} ({system}): уверенность {item.get('confidence')} ниже порога")
        else:
            p["system"] = system
            if p.get("kind") == "riser":
                p["label"] = PIPE_SYSTEMS[system]
            applied.append(f"труба {pid}: {system}, d {int(round(float(p.get('diameter', 0)) * 1000))} мм")
    for pid in pipes:
        if pid not in named:
            rejected.append(f"труба {pid}: модель не ответила")
    # deslab: 14.11.2026, Nagornaya hall: wires of the old wiring by the entrance door were built as heating and sewer pipes
    wet = _wet_polygons(new, review)
    radiators = [m for m in mep if m.get("kind") == "radiator" and m.get("start") and m.get("end")]
    big = [p for p in pipes.values() if p.get("kind") == "riser" and float(p.get("diameter", 0)) >= CABLE_DIAMETER]
    heating = [p for p in big if p.get("system") == "heating"]
    wet_risers = [p for p in big if p.get("system") in ("water", "sewer")]
    misplaced = []
    for p in pipes.values():
        why = _misplaced(p, wet, radiators, heating, wet_risers)
        if why:
            d = int(round(float(p["diameter"]) * 1000))
            rejected.append(f"труба {p['id']} ({p['system']}, {d} мм): {why}, не построена")
            if why != "кабель":
                misplaced.append(f"{p['id']} ({p['system']}, {d} мм)")
            del p["system"]
            p.pop("label", None)
            applied[:] = [a for a in applied if not a.startswith(f"труба {p['id']}:")]
    if misplaced:
        doubts.append("Не построены, потому что рядом нет санузла, кухни, радиатора или стояка: " + ", ".join(misplaced)
                      + ". Если это трубы, скажи, где кухня или мокрая зона.")
    # a box or a riser drawn from a candidate where the pipes themselves are built would draw the same thing twice
    risers = [p for p in pipes.values() if p.get("system") and p.get("kind") == "riser"]
    for m in list(mep):
        if m.get("candidate") and m.get("kind") in ("box", "shaft", "heatingRiser", "waterRiser", "sewerRiser"):
            pts = m.get("polygon") or [m.get("at")]
            cx, cy = sum(q["x"] for q in pts) / len(pts), sum(q["y"] for q in pts) / len(pts)
            if m.get("polygon"):
                # deslab: 14.11.2026, Nagornaya: a riser on the edge of a box beside it took that box away; a box goes
                # only for the risers that stand in it (its outline grown by the riser's radius and 3 cm); a duct only for
                # the risers right in it
                inside = [r for r in risers if _by_polygon((r["path"][0]["x"], r["path"][0]["y"]), m["polygon"],
                                                           0.0 if m.get("duct") else float(r.get("diameter", 0.05)) / 2 + 0.03)]
            else:
                inside = [r for r in risers if math.hypot(r["path"][0]["x"] - cx, r["path"][0]["y"] - cy) <= 0.15]
            sides = [math.dist((a["x"], a["y"]), (b["x"], b["y"])) for a, b in zip(pts, pts[1:] + pts[:1])] or [0.0]
            pipes_across = sum(float(r.get("diameter", 0.05)) for r in inside)
            # deslab: 13.11.2026, Nagornaya: a box 0.3 x 0.65 m standing full height in the bathroom corner is a box
            # of its own, the riser next to it does not explain it; only a box no longer than the risers in it is them.
            # 14.11.2026: a «duct» 14 x 38 cm at the outer wall was the two heating risers seen one behind the other, while
            # a thin pipe at the seam of the two real ducts stands in the wide one: a duct is its risers only when its
            # narrow side is no wider than they are and 10 cm
            if inside and (min(sides) > pipes_across + 0.1 if m.get("duct") else max(sides) > pipes_across + 0.3):
                continue
            if inside:
                mep.remove(m)
                applied[:] = [a for a in applied if f"из кандидата {m['candidate']}" not in a]
                rejected.append(f"{m['kind']} {m['id']} из кандидата {m['candidate']}: на этом месте построены сами трубы")
    # deslab: 14.11.2026, a thin «riser» right in a duct that stays is a seam or a flange of the duct (Nagornaya: 19 mm at the
    # joint of the two bathroom ducts, 1 cm apart; its label lay across the duct's)
    for m in mep:
        if not (m.get("duct") and m.get("polygon")):
            continue
        for p in risers:
            if p.get("system") and _point_in_polygon({"x": p["path"][0]["x"], "y": p["path"][0]["y"]}, m["polygon"]):
                del p["system"]
                p.pop("label", None)
                applied[:] = [a for a in applied if not a.startswith(f"труба {p['id']}:")]
                rejected.append(f"труба {p['id']}: стоит в воздуховоде {m['id']}, это его стык или фланец")
    # risers of one system that stand side by side (the pair by the window, the water risers) share one label:
    # three labels on one spot lay on each other (Nagornaya, 13.11.2026)
    labelled = []
    for p in pipes.values():
        if p.get("label"):
            at = p["path"][0]
            if any(q["label"] == p["label"] and math.hypot(q["path"][0]["x"] - at["x"], q["path"][0]["y"] - at["y"]) < 0.5 for q in labelled):
                del p["label"]
            else:
                labelled.append(p)
    for n, item in enumerate(review.get("vents", []) or [], 1):
        wall = walls.get(item.get("wallId"))
        try:
            along, height = float(item["along"]), float(item.get("height", 2.3))
        except (KeyError, TypeError, ValueError):
            rejected.append(f"вент. отверстие {n}: нет along")
            continue
        if not wall or item.get("side") not in ("left", "right") or not 0 <= along <= _wall_length(wall):
            rejected.append(f"вент. отверстие {n}: нет стены {item.get('wallId')}, грани или место вне стены")
        elif not _confident(item, min_confidence):
            rejected.append(f"вент. отверстие {n}: низкая уверенность")
        else:
            mep.append({"kind": "vent", "id": f"VO{n}", "at": _face_point(wall, item["side"], along), "height": round(height, 2),
                        "label": OFFICE_LABELS["vent"], "approximate": True})
            applied.append(f"вент. отверстие VO{n} на {wall['id']}")
    new["mep"] = mep

    for item in review.get("clutter", []) or []:
        wall = walls.get(item.get("wallId"))
        if not wall:
            rejected.append(f"мусор {item.get('wallId')}: нет такой стены")
        elif _wall_length(wall) > 1.0 and wall.get("kind") != "tracing":
            rejected.append(f"мусор {wall['id']}: стена длиннее 1 м, выбрасывать нельзя без Игоря")
        elif _carries_outline(new, wall):
            # deslab: JK 08.11.2026, the model dropped the box at the entrance (W5) while the measured room outline goes
            # round it: the outline then had no wall and the plan was refused; a wall the outline runs along stays
            rejected.append(f"мусор {wall['id']}: контур помещения обходит эту стену (короб или шахта), без Игоря не убираю")
        else:
            new["walls"] = [w for w in new["walls"] if w["id"] != wall["id"]]
            applied.append(f"убрана стена {wall['id']} (мусор): {item.get('reason', '')}")

    rooms = {r["id"]: r for r in new.get("rooms", [])}
    for item in review.get("rooms", []) or []:
        if item.get("id") in rooms and isinstance(item.get("name"), str) and item["name"].strip():
            rooms[item["id"]]["name"] = item["name"].strip()
            applied.append(f"помещение {item['id']}: «{item['name'].strip()}»")
        else:
            rejected.append(f"помещение {item.get('id')}: нет такого или пустое имя")
    wet = []
    for item in review.get("wetZones", []) or []:
        poly = item.get("polygon")
        if item.get("kind") in WET_KINDS and isinstance(poly, list) and len(poly) >= 3 and all(_is_point(p) and _inside_flat(p, new) for p in poly):
            wet.append(item)
            applied.append(f"мокрая зона {item['kind']}")
        else:
            rejected.append(f"мокрая зона {item.get('kind')}: неверные данные или не в квартире")
    if wet:
        new["wetZones"] = wet
    return new, {"applied": applied, "rejected": rejected, "doubts": list(review.get("doubts", []) or []) + doubts}


def main(argv=None):
    """python -m deslab_bridge.ai models | review <scene.json> [--hard] [--images a.png b.png] [--out review.json]
    Prints token use; never prints the key. `review` only asks and applies in memory, it builds nothing."""
    import argparse
    p = argparse.ArgumentParser(description="AI review of a measured-plan scene (Polza.ai).")
    p.add_argument("command", choices=("models", "review"))
    p.add_argument("scene", nargs="?")
    p.add_argument("--hard", action="store_true", help="the hard model (Opus) instead of the default one")
    p.add_argument("--model", help="any Polza model id, e.g. moonshotai/kimi-k2.5 (see `models`)")
    p.add_argument("--images", nargs="*", default=[])
    p.add_argument("--no-mep", action="store_true", help="do not send the existing mep items (a blind test)")
    p.add_argument("--out", help="where to save the review JSON")
    a = p.parse_args(argv)
    profile = json.loads((Path(__file__).resolve().parent.parent / "office-profile.json").read_text(encoding="utf-8"))
    try:
        if a.command == "models":
            for m in list_models(profile):
                print(m.get("id"))
            return 0
        scene = json.loads(Path(a.scene).read_text(encoding="utf-8"))
        started = time.time()
        review, usage, _ = ai_review(scene, a.images, profile, a.hard, with_mep=not a.no_mep, model=a.model)
        new, report = apply_review({k: v for k, v in scene.items() if not (a.no_mep and k in ("mep", "tracing"))}, review)
        print(f"{time.time() - started:.1f} с, токены: {usage}")
        print("применено:", len(report["applied"]), "отклонено:", len(report["rejected"]), "сомнений:", len(report["doubts"]))
        if a.out:
            Path(a.out).write_text(json.dumps({"review": review, "report": report, "usage": usage}, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    except AIError as error:
        print("ОШИБКА:", error)
        if getattr(error, "raw", None) is not None and a.out:
            Path(a.out + ".raw.txt").write_text(error.raw, encoding="utf-8")
            print("токены:", getattr(error, "usage", {}), "; ответ сохранён в", a.out + ".raw.txt")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
