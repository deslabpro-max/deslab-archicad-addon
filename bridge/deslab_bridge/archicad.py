"""Thin client for the Archicad JSON API and the Tapir-namespace commands of the deslab add-on."""
import json
import time
import urllib.error
import urllib.request


class ArchicadError(RuntimeError):
    pass


class Archicad:
    def __init__(self, port=19723, host="127.0.0.1", timeout=120.0):
        self.url = f"http://{host}:{port}"
        self.timeout = timeout
        self.log = []  # (command, seconds) of every call, for the timing report

    def _post(self, command, parameters, timeout):
        body = json.dumps({"command": command, "parameters": parameters}).encode("utf-8")
        request = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError) as error:
            if isinstance(error, TimeoutError) or "timed out" in str(error):
                raise ArchicadError(
                    f"Archicad не ответил на {command} за {timeout:.0f} с. Скорее всего, в Archicad открыт "
                    "диалог («Обзор папок», «Восстановление проекта», «Отсутствующие расширения»): его нужно закрыть."
                ) from error
            raise ArchicadError(f"Archicad не запущен или JSON API недоступен на {self.url}: {error}") from error
        finally:
            self.log.append((command if command != "API.ExecuteAddOnCommand" else parameters["addOnCommandId"]["commandName"],
                             round(time.perf_counter() - start, 3)))
        if not data.get("succeeded", False):
            raise ArchicadError(f"{command}: {data.get('error')}")
        return data.get("result", {})

    def api(self, command, parameters=None, timeout=None):
        return self._post(command, parameters or {}, timeout or self.timeout)

    def tapir(self, command, parameters=None, timeout=None):
        result = self.api("API.ExecuteAddOnCommand", {
            "addOnCommandId": {"commandNamespace": "TapirCommand", "commandName": command},
            "addOnCommandParameters": parameters or {},
        }, timeout)
        response = result.get("addOnCommandResponse", {})
        if isinstance(response, dict) and "error" in response:
            raise ArchicadError(f"{command}: {response['error']}")
        return response

    def alive(self):
        try:
            return bool(self.api("API.IsAlive", timeout=5).get("isAlive"))
        except ArchicadError:
            return False
