"""deslab: subscription transport contract, without network or an Archicad process."""
import base64
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deslab_bridge import ai, rebuild


def test_text_images_usage_and_isolation():
    variant = "gpt-6-astra"
    def run(command, **kwargs):
        assert command[:2] == ["codex.exe", "exec"]
        assert command[command.index("--model") + 1] == variant
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--ignore-user-config" in command and "--ephemeral" in command
        assert "--model" in command and command[-1] == "-"
        image = Path(command[command.index("--image") + 1])
        assert image.read_bytes() == b"test image bytes"
        assert image.parent == Path(kwargs["cwd"])
        assert "Сцена" in kwargs["input"].decode("utf-8")
        Path(command[command.index("--output-last-message") + 1]).write_text('{"doubts":[]}', encoding="utf-8")
        event = {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 8}}
        return subprocess.CompletedProcess(command, 0, json.dumps(event).encode(), b"warning")
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "Сцена"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"test image bytes").decode()}}]}]
    with patch.object(ai, "_codex_executable", return_value="codex.exe"), patch("subprocess.run", side_effect=run), \
         patch.object(ai, "api_key", side_effect=AssertionError("No Polza key needed")):
        for variant in ("gpt-6-astra", "gpt-6.1-sol"):
            answer, usage = ai.chat(messages, model="codex:" + variant)
            assert json.loads(answer) == {"doubts": []}
            assert usage["provider"] == "codex" and usage["input_tokens"] == 100
            assert usage["requested"] == variant and usage["models"] == [variant]


def test_failed_turn_and_exit_never_succeed():
    for returncode, output in [(0, b'{"type":"turn.failed","error":{"message":"SECRET"}}'),
                               (1, b''), (0, b'{"type":"turn.completed"}')]:
        with patch.object(ai, "_codex_executable", return_value="codex.exe"), \
             patch("subprocess.run", return_value=subprocess.CompletedProcess([], returncode, output, b"SECRET")):
            try:
                ai.chat([{"role": "user", "content": "test"}], model="codex:gpt-6-astra")
            except ai.AIError as error:
                assert "SECRET" not in str(error)
            else:
                raise AssertionError("A failed/empty run must not pass")


def test_no_ai_rejected_before_archicad():
    with patch.object(rebuild, "connect", side_effect=AssertionError("Do not connect")):
        try:
            rebuild.rebuild("test", preset={"ai": False})
        except ValueError as error:
            assert "без ИИ" in str(error)
        else:
            raise AssertionError("AI is required")
        try:
            rebuild.main(["test", "--no-ai"])
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("Removed CLI option must fail")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
