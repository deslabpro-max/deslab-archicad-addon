"""deslab-mcp: a small MCP server with a few large tools. Each tool does the whole job inside,
so Claude makes one call where it used to make hundreds (run: uv run deslab-mcp)."""
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import obmer
from .archicad import Archicad, ArchicadError

mcp = FastMCP("deslab", instructions=(
    "Мост deslab к Archicad 27. Обмерный план: analyze_scan (E57 → задание) → проверить задание, назвать помещения "
    "и мокрые зоны → build_measured_plan (одна команда строит модель и оформление по профилю офиса). "
    "Правила оформления в office-profile.json, их не надо повторять в вызовах. tapir — только для редких случаев."))
ac = Archicad()


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@mcp.tool()
def status() -> dict:
    """Archicad: отвечает ли, какой проект открыт, версия аддона."""
    if not ac.alive():
        return {"alive": False, "hint": "Archicad не отвечает: не запущен или открыт диалог."}
    try:
        info = ac.tapir("GetProjectInfo")
        version = ac.tapir("GetAddOnVersion")
    except ArchicadError as error:
        return {"alive": True, "error": str(error)}
    return {"alive": True, "project": info, "addOn": version}


@mcp.tool()
def analyze_scan(e57_path: str) -> dict:
    """Разбирает облако точек E57 на ПК (ничего не строит). Возвращает путь к заданию и его краткое описание."""
    scene_path = obmer.analyze_scan(ac, e57_path)
    scene = _read(scene_path)
    if "error" in scene:
        return {"scenePath": scene_path, "error": scene["error"]}
    return {"scenePath": scene_path, "summary": obmer.summarize_scene(scene)}


@mcp.tool()
def show_scene(scene_path: str) -> str:
    """Краткое описание задания: помещения, проёмы, допущения."""
    return obmer.summarize_scene(_read(scene_path))


@mcp.tool()
def build_measured_plan(scene_path: str, rooms: dict | None = None, origin_x: float = 0.0, origin_y: float = 0.0) -> dict:
    """Строит обмерный план в открытом проекте (копии шаблона) за один вызов: стены, проёмы, зоны с номером и «Н»,
    выноски высот проёмов, размерные цепочки, мокрые зоны, слои, обновление чертежей на листах.
    rooms: {"R1": {"name": "Санузел", "wet": "bathroom"}, "R2": {"name": "Кухня-гостиная", "wet": "kitchen"}}."""
    scene = _read(scene_path)
    return obmer.build_measured_plan(ac, scene, rooms=rooms, origin={"x": origin_x, "y": origin_y})


@mcp.tool()
def tapir(command: str, parameters: dict | None = None) -> dict:
    """Любая команда аддона (пространство Tapir) напрямую. Только когда крупных инструментов не хватает."""
    return ac.tapir(command, parameters or {})


def main():
    mcp.run()


if __name__ == "__main__":
    main()
