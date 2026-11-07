"""deslab: chat with the model about the last build (Игорь 06.11.2026: «там же общаемся в чате с моделью»).

The «deslab Обмер» palette in Archicad runs `python -m deslab_bridge.chat <name> "<message>" [--model id]` and shows what
it prints. The model gets the AI review of that build (captures/ai-review-<name>.json) and the chat so far
(captures/chat-<name>.json), and answers in plain Russian. It changes nothing by itself: a correction is applied by a new
build. The key is never printed (ai.py cleans it out of every error)."""
import argparse
import json
import sys
from pathlib import Path

from . import ai
from .rebuild import _root

HISTORY = 20          # messages of the chat kept for the model
REVIEW_CHARS = 30000  # the review is cut to this size

SYSTEM = ("Ты помощник архитектора-обмерщика в офисе deslab. Ниже разбор последнего обмера, который сделала модель "
          "(найденное, применённое, отклонённое проверками, сомнения). Отвечай по-русски, коротко и по делу, без Markdown. "
          "Если пользователь просит правку, скажи, что именно поменять (элемент, координаты в метрах, тип), "
          "и что правка войдёт в следующую сборку кнопкой «Построить».")


def run(name, message, root=None, model=None, call=ai.chat):
    out_dir = _root(root) / "captures"
    review_path = out_dir / f"ai-review-{name}.json"
    history_path = out_dir / f"chat-{name}.json"
    review = review_path.read_text(encoding="utf-8")[:REVIEW_CHARS] if review_path.exists() else "(разбора ещё нет)"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    profile = json.loads((Path(__file__).resolve().parent.parent / "office-profile.json").read_text(encoding="utf-8"))
    messages = [{"role": "system", "content": SYSTEM + "\n\nРазбор (JSON):\n" + review}]
    messages += history[-HISTORY:]
    messages.append({"role": "user", "content": message})
    text, _usage = call(messages, profile, model, json_mode=False)
    history += [{"role": "user", "content": message}, {"role": "assistant", "content": text}]
    out_dir.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    return text


def main(argv=None):
    p = argparse.ArgumentParser(description="Чат с моделью о последней сборке.")
    p.add_argument("name", help="имя сборки (пресет или имя файла .e57 без расширения)")
    p.add_argument("message")
    p.add_argument("--model")
    p.add_argument("--root")
    a = p.parse_args(argv)
    try:
        print(run(a.name, a.message, a.root, a.model))
        return 0
    except ai.AIError as error:
        print("ОШИБКА:", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
