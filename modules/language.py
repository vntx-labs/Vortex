#!/usr/bin/env python3
"""Small, allowlisted interpreter for Vortex .vscript module programs."""

from __future__ import annotations

import base64
import binascii
import html
import json
import os
import pwd
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class ScriptError(ValueError):
    """Raised when a module script is invalid or a host action fails."""


@dataclass(frozen=True)
class Instruction:
    operation: str
    arguments: tuple[str, ...]
    line: int


def parse_script(source: str) -> list[Instruction]:
    if len(source) > 1_000_000:
        raise ScriptError("Modulskript überschreitet das Limit von 1 MB.")
    instructions = []
    blocks: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(source.splitlines(), 1):
        if len(raw_line) > 16_384:
            raise ScriptError(f"Zeile {line_number}: Zeile ist zu lang.")
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens = shlex.split(line)
        except ValueError as error:
            raise ScriptError(f"Zeile {line_number}: {error}") from error
        operation, *arguments = tokens
        if operation == "if":
            if (
                len(arguments) not in {2, 3}
                or arguments[0] not in {"empty", "failed", "declined", "equals"}
                or (arguments[0] == "equals" and len(arguments) != 3)
                or (arguments[0] != "equals" and len(arguments) != 2)
            ):
                raise ScriptError(
                    f"Zeile {line_number}: erwartet 'if empty NAME', 'if failed last', "
                    "'if declined NAME' oder 'if equals NAME VALUE'."
                )
            if arguments[0] == "failed" and arguments[1] != "last":
                raise ScriptError(f"Zeile {line_number}: 'if failed' erwartet nur 'last'.")
            blocks.append({"else": False, "line": line_number, "type": "if"})
        elif operation == "else":
            if (
                arguments
                or not blocks
                or blocks[-1]["type"] != "if"
                or blocks[-1]["else"]
            ):
                raise ScriptError(f"Zeile {line_number}: unerwartetes 'else'.")
            blocks[-1]["else"] = True
        elif operation == "loop":
            if arguments:
                raise ScriptError(f"Zeile {line_number}: 'loop' akzeptiert keine Argumente.")
            blocks.append({"line": line_number, "type": "loop"})
        elif operation == "break":
            if arguments or not any(block["type"] == "loop" for block in blocks):
                raise ScriptError(f"Zeile {line_number}: 'break' muss in einer Schleife stehen.")
        elif operation == "end":
            if arguments or not blocks:
                raise ScriptError(f"Zeile {line_number}: unerwartetes 'end'.")
            blocks.pop()
        elif operation == "call":
            if len(arguments) < 1:
                raise ScriptError(f"Zeile {line_number}: 'call' benötigt eine Aktion.")
        elif operation == "return":
            if len(arguments) > 1:
                raise ScriptError(f"Zeile {line_number}: 'return' akzeptiert höchstens einen Status.")
            if arguments:
                try:
                    code = int(arguments[0])
                except ValueError as error:
                    raise ScriptError(f"Zeile {line_number}: Rückgabestatus muss eine Zahl sein.") from error
                if not 0 <= code <= 255:
                    raise ScriptError(f"Zeile {line_number}: Rückgabestatus muss zwischen 0 und 255 liegen.")
        else:
            raise ScriptError(f"Zeile {line_number}: unbekannter Befehl {operation!r}.")
        instructions.append(Instruction(operation, tuple(arguments), line_number))
    if blocks:
        raise ScriptError(f"Zeile {blocks[-1]['line']}: 'if' wird nicht mit 'end' geschlossen.")
    return instructions


def _expand(value: str, variables: dict[str, str], line: int) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise ScriptError(f"Zeile {line}: unbekannte Variable {name!r}.")
        return variables[name]

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)


def _snippet_store_path() -> Path:
    return Path.home() / ".local" / "share" / "velos" / "snippets.tsv"


def _read_snippet_rows(path: Path) -> list[str]:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("Der Snippet-Speicher ist keine reguläre Datei.")
        with os.fdopen(descriptor, "r", encoding="ascii") as source:
            descriptor = -1
            return source.read().splitlines()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _decode_snippet_row(row: str) -> tuple[str, str] | None:
    try:
        encoded_title, encoded_body = row.split("\t", 1)
        title_bytes = base64.b64decode(
            encoded_title + "=" * (-len(encoded_title) % 4),
            altchars=b"-_",
            validate=True,
        )
        body_bytes = base64.b64decode(
            encoded_body + "=" * (-len(encoded_body) % 4),
            altchars=b"-_",
            validate=True,
        )
        return title_bytes.decode("utf-8"), body_bytes.decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None


def _encode_snippet(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _get_snippet(index_text: str) -> tuple[str, str]:
    if not index_text.isdigit() or int(index_text) < 1:
        raise ValueError("Ungültige Snippet-ID.")
    rows = _read_snippet_rows(_snippet_store_path())
    index = int(index_text) - 1
    if index >= len(rows):
        raise IndexError("Die Snippet-ID ist nicht mehr vorhanden.")
    decoded = _decode_snippet_row(rows[index])
    if decoded is None:
        raise ValueError("Der gespeicherte Snippet ist beschädigt.")
    return decoded


def _write_snippet_rows(path: Path, rows: list[str]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".snippets-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as output:
            descriptor = -1
            output.write("".join(row + "\n" for row in rows))
        os.replace(temporary, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _save_snippet(index_text: str, title: str, body: str) -> None:
    path = _snippet_store_path()
    encoded = f"{_encode_snippet(title)}\t{_encode_snippet(body)}"
    if index_text == "new":
        rows = _read_snippet_rows(path)
        _write_snippet_rows(path, [*rows, encoded])
        return
    if not index_text.isdigit() or int(index_text) < 1:
        raise ValueError("Ungültige Snippet-ID.")
    rows = _read_snippet_rows(path)
    index = int(index_text) - 1
    if index >= len(rows):
        raise IndexError("Der Eintrag wurde zwischenzeitlich geändert.")
    rows[index] = encoded
    _write_snippet_rows(path, rows)


def _delete_snippet(index_text: str) -> None:
    if not index_text.isdigit() or int(index_text) < 1:
        raise ValueError("Ungültige Snippet-ID.")
    path = _snippet_store_path()
    rows = _read_snippet_rows(path)
    index = int(index_text) - 1
    if index >= len(rows):
        raise IndexError("Die Snippet-ID ist nicht mehr vorhanden.")
    rows.pop(index)
    _write_snippet_rows(path, rows)


def _host_call(host_app: str, action: str, arguments: list[str]) -> tuple[int, str]:
    allowed = {
        "ensure_optional_tool",
        "ensure_python_feature",
        "pick_path",
        "show_error",
    }
    if action not in allowed:
        raise ScriptError(f"Nicht unterstützte Host-Aktion: {action}")
    if action == "show_error" and len(arguments) == 1:
        arguments = [html.escape(arguments[0])]
    try:
        result = subprocess.run(
            ["bash", host_app, "--module-api", action, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ScriptError(f"Host-Aktion {action} konnte nicht ausgeführt werden: {error}") from error
    if result.returncode and result.stderr.strip():
        raise ScriptError(result.stderr.strip())
    return result.returncode, result.stdout.rstrip("\n")


def _run_action(
    action: str,
    arguments: list[str],
    variables: dict[str, str],
    host_app: str,
) -> tuple[int, str]:
    if action == "snippets.initialize":
        if arguments:
            raise ScriptError("snippets.initialize akzeptiert keine Argumente.")
        try:
            store = _snippet_store_path()
            previous_umask = os.umask(0o077)
            try:
                store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            finally:
                os.umask(previous_umask)
            descriptor = os.open(
                store,
                os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NONBLOCK
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    return 1, "Der Snippet-Speicher ist keine reguläre Datei."
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
        except OSError as error:
            return 1, f"Der Snippet-Speicher konnte nicht vorbereitet werden: {error}"
        return 0, ""

    if action == "snippets.list":
        if len(arguments) != 1:
            raise ScriptError("snippets.list erwartet eine Ergebnisvariable.")
        try:
            rows = _read_snippet_rows(_snippet_store_path())
            items = []
            for index, row in enumerate(rows, 1):
                decoded = _decode_snippet_row(row)
                if decoded is not None:
                    title, _ = decoded
                    items.append([str(index), title])
            variables[arguments[0]] = json.dumps(items, ensure_ascii=False)
        except (OSError, UnicodeError) as error:
            return 1, f"Snippet-Liste konnte nicht gelesen werden: {error}"
        return 0, ""

    if action == "snippets.get":
        if len(arguments) != 3:
            raise ScriptError("snippets.get erwartet Ergebnisvariablen für Name und Inhalt sowie eine ID.")
        title_name, body_name, index_text = arguments
        try:
            title, body = _get_snippet(index_text)
        except (OSError, ValueError, IndexError) as error:
            return 1, f"Der ausgewählte Snippet konnte nicht gelesen werden: {error}"
        variables[title_name] = title
        variables[body_name] = body
        return 0, ""

    if action == "snippets.save":
        if len(arguments) != 3:
            raise ScriptError("snippets.save erwartet ID (oder new), Name und Inhalt.")
        index_text, title, body = arguments
        if not title:
            return 1, "Der Snippet-Name darf nicht leer sein."
        try:
            _save_snippet(index_text, title, body)
        except (OSError, ValueError, IndexError) as error:
            return 1, f"Snippet konnte nicht gespeichert werden: {error}"
        return 0, ""

    if action == "snippets.delete":
        if len(arguments) != 1:
            raise ScriptError("snippets.delete erwartet eine Snippet-ID.")
        try:
            _delete_snippet(arguments[0])
        except (OSError, ValueError, IndexError) as error:
            return 1, f"Snippet konnte nicht gelöscht werden: {error}"
        return 0, ""

    if action == "system.clipboard":
        if len(arguments) != 1:
            raise ScriptError("system.clipboard erwartet den zu kopierenden Text.")
        if shutil.which("wl-copy"):
            command = ["wl-copy"]
        elif shutil.which("xclip"):
            command = ["xclip", "-selection", "clipboard"]
        elif shutil.which("xsel"):
            command = ["xsel", "--clipboard", "--input"]
        else:
            status, error = _host_call(
                host_app,
                "ensure_optional_tool",
                ["xclip", "Zwischenablage-Unterstützung", "xclip"],
            )
            if status:
                return status, error
            command = ["xclip", "-selection", "clipboard"]
        try:
            result = subprocess.run(
                command,
                input=arguments[0],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return 1, f"Die Zwischenablage ist nicht erreichbar: {error}"
        if result.returncode:
            return 1, result.stderr.strip() or "Text konnte nicht in die Zwischenablage kopiert werden."
        return 0, ""

    if action == "ui.list":
        if len(arguments) != 6:
            raise ScriptError("ui.list erwartet Auswahl, Schaltflächenstatus, Titel, Beschreibung, Schaltflächen und Zeilen.")
        selected_name, response_name, title, description, buttons_json, rows_json = arguments
        try:
            buttons = json.loads(buttons_json)
            rows = json.loads(rows_json)
        except json.JSONDecodeError as error:
            return 1, f"Listenansicht enthält ungültige Daten: {error}"
        if (
            not isinstance(buttons, list)
            or not all(isinstance(button, str) and ":" in button for button in buttons)
            or not isinstance(rows, list)
            or not all(
                isinstance(row, list)
                and len(row) == 2
                and all(isinstance(value, str) for value in row)
                for row in rows
            )
        ):
            return 1, "Listenansicht enthält ungültige Schaltflächen oder Zeilen."
        command = [
            "yad", "--list", "--center", "--on-top", "--modal",
            f"--title={title}", f"--text={html.escape(description)}",
            "--column=ID", "--column=Snippet", "--hide-column=1",
            "--print-column=1", "--separator=", "--width=760", "--height=520",
        ]
        command.extend(f"--button={button}" for button in buttons)
        command.extend(value for row in rows for value in row)
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return 1, f"Snippet-Liste konnte nicht geöffnet werden: {error}"
        if result.returncode not in {0, 1, 2, 3, 4}:
            return 1, result.stderr.strip() or f"Snippet-Liste wurde mit Status {result.returncode} geschlossen."
        selected = result.stdout.strip()
        variables[selected_name] = selected if selected.isdigit() else ""
        variables[response_name] = str(result.returncode)
        return 0, ""

    if action == "ui.edit_text":
        if len(arguments) != 5:
            raise ScriptError("ui.edit_text erwartet Ergebnis, Status, Titel, Beschreibung und Inhalt.")
        result_name, response_name, title, description, content = arguments
        try:
            with tempfile.TemporaryDirectory(prefix="velos-snippet-") as temporary_dir:
                os.chmod(temporary_dir, 0o700)
                body_path = Path(temporary_dir, "body.txt")
                body_path.write_text(content, encoding="utf-8", newline="")
                os.chmod(body_path, 0o600)
                result = subprocess.run(
                    [
                        "yad", "--text-info", "--editable", "--center", "--on-top", "--modal",
                        f"--title={title}", f"--text={html.escape(description)}",
                        f"--filename={body_path}", "--width=820", "--height=560",
                        "--button=Abbrechen:1", "--button=Speichern:0",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
        except (OSError, subprocess.SubprocessError) as error:
            return 1, f"Texteditor konnte nicht geöffnet werden: {error}"
        variables[result_name] = result.stdout if result.returncode == 0 else ""
        if result.returncode not in {0, 1}:
            return 1, result.stderr.strip() or f"Texteditor wurde mit Status {result.returncode} geschlossen."
        variables[response_name] = "yes" if result.returncode == 0 else "no"
        return 0, ""

    if action == "ui.entry":
        if len(arguments) not in {4, 5}:
            raise ScriptError("ui.entry erwartet Ergebnisvariable, optional Statusvariable, Titel, Beschreibung und Vorgabewert.")
        if len(arguments) == 5:
            result_name, response_name, title, prompt, default = arguments
        else:
            result_name, title, prompt, default = arguments
            response_name = ""
        try:
            result = subprocess.run(
                [
                    "yad", "--entry", "--center", "--on-top", "--modal",
                    f"--title={title}", f"--text={html.escape(prompt)}",
                    f"--entry-text={default}", "--width=560",
                    "--button=Abbrechen:1", "--button=Weiter:0",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return 1, f"Eingabedialog konnte nicht geöffnet werden: {error}"
        if result.returncode == 0:
            variables[result_name] = result.stdout.rstrip("\n")
            if response_name:
                variables[response_name] = "yes"
        elif result.returncode == 1:
            variables[result_name] = ""
            if response_name:
                variables[response_name] = "no"
        else:
            return 1, result.stderr.strip() or f"Eingabedialog wurde mit Status {result.returncode} geschlossen."
        return 0, ""

    if action == "process.ensure_port_tool":
        if arguments:
            raise ScriptError("process.ensure_port_tool akzeptiert keine Argumente.")
        if shutil.which("lsof") or shutil.which("fuser"):
            return 0, ""
        return _host_call(host_app, "ensure_optional_tool", ["lsof", "Port-Erkennung", "lsof"])

    if action == "process.validate_port":
        if len(arguments) != 1:
            raise ScriptError("process.validate_port erwartet einen Port.")
        if not arguments[0].isdigit() or not 1 <= int(arguments[0]) <= 65535:
            return 1, "Bitte einen gültigen Port zwischen 1 und 65535 eingeben."
        return 0, ""

    if action == "process.listener_pids":
        if len(arguments) != 2:
            raise ScriptError("process.listener_pids erwartet Ergebnisvariable und Port.")
        result_name, port_text = arguments
        status, result = _listener_pids(port_text)
        if status:
            return status, result
        variables[result_name] = result
        return 0, ""

    if action == "ui.select_port_process":
        if len(arguments) != 3:
            raise ScriptError("ui.select_port_process erwartet Ergebnisvariable, Port und PID-Liste.")
        result_name, port_text, pids_text = arguments
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            return 1, "Ungültiger Port für die Prozessauswahl."
        pids = sorted(
            {value for value in re.findall(r"\b[0-9]+\b", pids_text)
             if int(value) > 0},
            key=int,
        )
        rows = []
        for pid in pids:
            try:
                with open(f"/proc/{pid}/status", encoding="utf-8") as source:
                    uid = next(
                        int(line.split()[1])
                        for line in source
                        if line.startswith("Uid:")
                    )
                with open(f"/proc/{pid}/comm", encoding="utf-8") as source:
                    command = source.read().strip()
                with open(f"/proc/{pid}/cmdline", "rb") as source:
                    arguments_text = (
                        source.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                    )
                username = pwd.getpwuid(uid).pw_name
            except (OSError, StopIteration, KeyError, ValueError):
                continue
            fields = (pid, username, command, arguments_text)
            rows.append("\t".join(
                value.replace("\t", " ").replace("\r", " ").replace("\n", " ")
                for value in fields
            ))
        if not rows:
            variables[result_name] = ""
            return 0, ""
        try:
            result = subprocess.run(
                [
                    "yad", "--list", "--center", "--on-top", "--modal",
                    f"--title=Prozess auf Port {port_text}",
                    "--text=Wähle genau einen Prozess. Erst wird SIGTERM gesendet; "
                    "SIGKILL erfordert eine weitere Bestätigung.",
                    "--column=PID", "--column=Benutzer", "--column=Programm", "--column=Befehl",
                    "--print-column=1", "--separator=", "--width=900", "--height=420",
                    "--button=Abbrechen:1", "--button=Prozess beenden:0",
                ],
                input="".join(row + "\n" for row in rows),
                check=False,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return 1, f"Prozessauswahl konnte nicht geöffnet werden: {error}"
        selected = result.stdout.strip()
        variables[result_name] = (
            selected if result.returncode == 0 and selected in pids else ""
        )
        return 0, ""

    if action == "process.terminate_listener":
        if len(arguments) != 3:
            raise ScriptError(
                "process.terminate_listener erwartet Port, PID und Bestätigungsvariable."
            )
        port_text, pid_text, confirmation_name = arguments
        if (
            not port_text.isdigit() or not 1 <= int(port_text) <= 65535
            or not pid_text.isdigit() or int(pid_text) <= 0
        ):
            return 1, "Ungültiger Port oder Prozess für die Beendigung."
        if variables.get(confirmation_name) != "yes":
            return 1, "SIGTERM wurde nicht ausdrücklich bestätigt."
        result_name = "listener_still_running"
        status, pids_text = _listener_pids(port_text)
        if status:
            return status, pids_text
        if pid_text not in pids_text.split():
            return 1, f"PID {pid_text} blockiert Port {port_text} nicht mehr. Bitte erneut prüfen."
        try:
            os.kill(int(pid_text), 0)
            os.kill(int(pid_text), signal.SIGTERM)
        except OSError as error:
            return 1, f"SIGTERM konnte nicht an PID {pid_text} gesendet werden: {error}"
        for _ in range(20):
            try:
                os.kill(int(pid_text), 0)
            except ProcessLookupError:
                variables[result_name] = "no"
                return 0, ""
            except PermissionError:
                break
            time.sleep(0.1)
        variables[result_name] = "yes"
        return 0, ""

    if action == "process.kill_selected":
        if (
            len(arguments) != 2
            or not arguments[0].isdigit() or int(arguments[0]) <= 0
        ):
            raise ScriptError("process.kill_selected erwartet PID und Bestätigungsvariable.")
        pid_text, confirmation_name = arguments
        if variables.get(confirmation_name) != "yes":
            return 1, "SIGKILL wurde nicht ausdrücklich bestätigt."
        pid = int(pid_text)
        try:
            os.kill(pid, 0)
        except OSError as error:
            return 1, f"PID {pid} ist nicht mehr aktiv: {error}"
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as error:
            return 1, f"SIGKILL konnte nicht an PID {pid} gesendet werden: {error}"
        return 0, ""

    if action == "core.pick_path":
        if len(arguments) != 3:
            raise ScriptError("core.pick_path erwartet Variable, Typ und Beschreibung.")
        name, kind, prompt = arguments
        status, value = _host_call(host_app, "pick_path", [kind, prompt])
        variables[name] = value
        return status, ""
    if action.startswith("core."):
        return _host_call(host_app, action[5:], arguments)

    if action in {
        "module.file_tools.replace",
        "module.file_tools.convert",
        "module.local_safe",
        "module.local_server",
    }:
        try:
            if __package__:
                from .module_actions import dispatch
            else:
                from module_actions import dispatch
            return dispatch(action, host_app, arguments)
        except (OSError, subprocess.SubprocessError) as error:
            return 1, f"Modulaktion konnte nicht ausgeführt werden: {error}"

    gui_helpers = {
        "gui.csv_workspace": "csv_workspace.py",
        "gui.focus_code_editor": "focus_code_editor.py",
        "gui.web_studio": "web_studio.py",
    }
    if action in gui_helpers:
        if arguments:
            raise ScriptError(f"{action} akzeptiert keine Argumente.")
        helper = Path(__file__).resolve().parent / gui_helpers[action]
        environment = os.environ.copy()
        environment["VORTEX_HOST_APP"] = host_app
        if action == "gui.focus_code_editor":
            for package_manager in ("apt-get", "dnf", "pacman", "zypper", "apk"):
                if shutil.which(package_manager):
                    environment["VELOOS_PACKAGE_MANAGER"] = package_manager
                    break
        try:
            result = subprocess.run(
                [shutil.which("python3") or "python3", str(helper)],
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return 1, f"GUI-Modul konnte nicht gestartet werden: {error}"
        return result.returncode, ""

    if action == "fs.realpath":
        if len(arguments) != 1 or arguments[0] not in variables:
            raise ScriptError("fs.realpath erwartet einen Variablennamen.")
        name = arguments[0]
        try:
            variables[name] = str(Path(variables[name]).resolve(strict=True))
        except OSError as error:
            return 1, str(error)
        return 0, ""

    if action == "fs.assert_disjoint":
        if len(arguments) != 2:
            raise ScriptError("fs.assert_disjoint erwartet zwei Pfadvariablen.")
        try:
            paths = [Path(variables[name]).resolve(strict=True) for name in arguments]
            if paths[0] == paths[1] or paths[0] in paths[1].parents or paths[1] in paths[0].parents:
                return 1, "Quell- und Zielordner dürfen weder identisch sein noch ineinander liegen."
        except (KeyError, OSError) as error:
            return 1, f"Ordner konnten nicht geprüft werden: {error}"
        return 0, ""

    if action == "fs.temp":
        if len(arguments) != 2:
            raise ScriptError("fs.temp erwartet Zielvariable und Präfix.")
        name, prefix = arguments
        try:
            with tempfile.NamedTemporaryFile(
                prefix=prefix, dir=os.environ.get("TMPDIR") or None, delete=False
            ) as temporary:
                variables[name] = temporary.name
        except OSError as error:
            return 1, f"Temporäre Datei konnte nicht erstellt werden: {error}"
        return 0, ""

    if action == "fs.compose_preview":
        if len(arguments) != 4:
            raise ScriptError("fs.compose_preview erwartet Ausgabe, Protokoll, Quelle und Ziel.")
        output_name, log_name, source_name, destination_name = arguments
        try:
            with open(variables[log_name], encoding="utf-8", errors="replace") as log:
                preview = log.read(2_000_000)
            with open(variables[output_name], "w", encoding="utf-8") as output:
                output.write(
                    f"Quelle: {variables[source_name]}\nZiel: {variables[destination_name]}\n\n"
                )
                output.writelines(preview.splitlines(keepends=True)[:180])
                output.write("\nVorschau gekürzt, falls weitere Einträge folgen.\n")
        except (KeyError, OSError) as error:
            return 1, f"Backup-Vorschau konnte nicht vorbereitet werden: {error}"
        return 0, ""

    if action == "fs.tail":
        if len(arguments) != 3:
            raise ScriptError("fs.tail erwartet Protokoll, Zielvariable und Zeilenanzahl.")
        path_name, output_name, count_text = arguments
        try:
            count = min(max(int(count_text), 1), 100)
            with open(variables[path_name], encoding="utf-8", errors="replace") as source:
                variables[output_name] = "".join(deque(source, maxlen=count))
        except (KeyError, OSError, ValueError) as error:
            return 1, f"Protokoll konnte nicht gelesen werden: {error}"
        return 0, ""

    if action == "fs.remove":
        try:
            for name in arguments:
                os.unlink(variables[name])
                del variables[name]
        except (KeyError, OSError) as error:
            return 1, f"Temporäre Datei konnte nicht entfernt werden: {error}"
        return 0, ""

    if action == "process.capture":
        if len(arguments) < 4:
            raise ScriptError("process.capture erwartet Ausgabevariable, Sekunden, Programm und Argumente.")
        output_name, timeout_text, *command = arguments
        try:
            with open(variables[output_name], "w", encoding="utf-8") as output:
                result = subprocess.run(
                    command,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=float(timeout_text),
                )
        except (KeyError, OSError, ValueError, subprocess.TimeoutExpired) as error:
            return 1, f"Vorschau fehlgeschlagen oder Zeitlimit überschritten: {error}"
        return result.returncode, ""

    if action == "process.progress":
        if len(arguments) != 2:
            raise ScriptError("process.progress erwartet Quell- und Zielpfadvariablen.")
        try:
            command = [
                "nice", "-n", "10", "rsync", "-a", "--delete", "--info=progress2",
                "--", variables[arguments[0]] + "/", variables[arguments[1]] + "/",
            ]
            with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as error_log:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                )
                if process.stdout is None:
                    process.terminate()
                    process.wait()
                    return 1, "rsync-Ausgabe konnte nicht gelesen werden."
                try:
                    progress = subprocess.Popen(
                        [
                            "yad", "--progress", "--center", "--on-top", "--modal",
                            "--title=Backup läuft", "--text=Ordner werden synchronisiert…",
                            "--pulsate", "--auto-close", "--no-cancel", "--width=520",
                        ],
                        stdin=subprocess.PIPE,
                        text=True,
                    )
                except OSError:
                    process.terminate()
                    process.wait()
                    raise
                if progress.stdin is None:
                    process.terminate()
                    process.wait()
                    progress.terminate()
                    progress.wait()
                    return 1, "Fortschrittsdialog konnte nicht geöffnet werden."
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        while selector.get_map():
                            for key, _ in selector.select(timeout=0.5):
                                chunk = os.read(key.fd, 4096)
                                if not chunk:
                                    selector.unregister(key.fileobj)
                                    continue
                                text = chunk.decode("utf-8", errors="replace")
                                error_log.write(text)
                                progress.stdin.write(text)
                                progress.stdin.flush()
                except (BrokenPipeError, OSError) as error:
                    process.terminate()
                    process.wait()
                    progress.terminate()
                    progress.wait()
                    return 1, f"Fortschrittsdialog wurde unerwartet geschlossen: {error}"
                progress.stdin.close()
                progress.wait()
                process.wait()
                process.stdout.close()
                error_log.seek(0)
                error_output = "".join(deque(error_log, maxlen=30)).strip()
        except (KeyError, OSError, subprocess.SubprocessError) as error:
            return 1, f"Backup konnte nicht ausgeführt werden: {error}"
        if process.returncode:
            detail = f"rsync-Status {process.returncode}"
            if error_output:
                detail = f"{detail}\n{error_output}"
            return process.returncode, detail
        return 0, ""

    if action in {"ui.text_info", "ui.confirm", "ui.info", "ui.error"}:
        return _run_dialog(action, arguments, variables)

    raise ScriptError(f"Unbekannte DSL-Aktion {action!r}.")


def _listener_pids(port_text: str) -> tuple[int, str]:
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        return 1, "Bitte einen gültigen Port zwischen 1 und 65535 eingeben."
    if shutil.which("lsof"):
        command = ["lsof", "-nP", "-t", f"-iTCP:{port_text}", "-sTCP:LISTEN"]
    elif shutil.which("fuser"):
        command = ["fuser", "-n", "tcp", port_text]
    else:
        return 1, "Weder lsof noch fuser ist verfügbar."
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return 1, f"Port konnte nicht geprüft werden: {error}"
    if result.returncode not in {0, 1} or (
        result.returncode == 1 and result.stderr.strip()
    ):
        detail = result.stderr.strip() or result.stdout.strip()
        return 1, f"Port-Erkennung ist fehlgeschlagen: {detail or result.returncode}"
    pids = sorted(
        {value for value in re.findall(r"\b[0-9]+\b", result.stdout)
         if int(value) > 0},
        key=int,
    )
    return 0, "\n".join(pids)


def _run_dialog(action: str, arguments: list[str], variables: dict[str, str]) -> tuple[int, str]:
    if action == "ui.text_info":
        if len(arguments) < 3:
            raise ScriptError("ui.text_info erwartet Ergebnisvariable, Datei und Titel.")
        result_name, file_name, title, *buttons = arguments
        command = ["yad", "--text-info", "--center", "--on-top", "--modal",
                   f"--title={title}", f"--filename={variables[file_name]}",
                   "--width=820", "--height=500"]
        command.extend(f"--button={button}" for button in buttons)
        result = subprocess.run(command, check=False)
        variables[result_name] = str(result.returncode)
        return 0, ""
    if action == "ui.confirm":
        if len(arguments) != 3:
            raise ScriptError("ui.confirm erwartet Ergebnisvariable, Titel und Text.")
        result_name, title, text = arguments
        result = subprocess.run(
            ["yad", "--question", "--center", "--on-top", "--modal",
             f"--title={title}", f"--text={html.escape(text)}", "--width=680"],
            check=False,
        )
        variables[result_name] = "yes" if result.returncode == 0 else "no"
        return 0, ""
    if action in {"ui.info", "ui.error"}:
        if len(arguments) != 2:
            raise ScriptError(f"{action} erwartet Titel und Text.")
        title, text = arguments
        kind = "info" if action == "ui.info" else "error"
        result = subprocess.run(
            ["yad", f"--{kind}", "--center", "--on-top", "--modal",
             f"--title={title}", f"--text={html.escape(text)}", "--width=560"],
            check=False,
        )
        return 0, ""
    raise ScriptError(f"Unbekannter Dialog {action!r}.")


Action = Callable[[str, list[str], dict[str, str], str], tuple[int, str]]


def run_script(
    source: str,
    host_app: str,
    action_runner: Action = _run_action,
) -> int:
    instructions = parse_script(source)
    block_pairs: dict[int, int] = {}
    open_blocks: list[int] = []
    for index, instruction in enumerate(instructions):
        if instruction.operation in {"if", "loop"}:
            open_blocks.append(index)
        elif instruction.operation == "end":
            start = open_blocks.pop()
            block_pairs[start] = index
            block_pairs[index] = start
    variables: dict[str, str] = {}
    blocks: list[dict[str, object]] = []
    last_status = 0
    active = True
    instruction_index = 0
    while instruction_index < len(instructions):
        instruction = instructions[instruction_index]
        operation, arguments = instruction.operation, list(instruction.arguments)
        if operation == "if":
            condition, *condition_args = arguments
            if active:
                if condition == "failed":
                    value = last_status != 0
                elif condition == "equals":
                    name, expected = condition_args
                    if name not in variables:
                        raise ScriptError(
                            f"Zeile {instruction.line}: unbekannte Variable {name!r}."
                        )
                    value = variables[name] == expected
                elif condition == "empty":
                    if condition_args[0] not in variables:
                        raise ScriptError(
                            f"Zeile {instruction.line}: unbekannte Variable {condition_args[0]!r}."
                        )
                    value = not variables.get(condition_args[0], "")
                else:
                    if condition_args[0] not in variables:
                        raise ScriptError(
                            f"Zeile {instruction.line}: unbekannte Variable {condition_args[0]!r}."
                        )
                    value = variables.get(condition_args[0]) not in {"yes", "0"}
            else:
                value = False
            blocks.append(
                {"parent": active, "match": value, "else": False, "type": "if"}
            )
            active = active and value
            instruction_index += 1
            continue
        if operation == "else":
            frame = blocks[-1]
            frame["else"] = True
            active = bool(frame["parent"]) and not bool(frame["match"])
            instruction_index += 1
            continue
        if operation == "loop":
            if active:
                blocks.append(
                    {
                        "parent": active,
                        "type": "loop",
                        "start": instruction_index,
                    }
                )
            instruction_index += 1
            continue
        if operation == "break":
            if active:
                loop_position = next(
                    (
                        position
                        for position in range(len(blocks) - 1, -1, -1)
                        if blocks[position]["type"] == "loop"
                    ),
                    -1,
                )
                if loop_position < 0:
                    raise ScriptError(
                        f"Zeile {instruction.line}: 'break' wurde außerhalb einer aktiven Schleife ausgeführt."
                    )
                loop_frame = blocks[loop_position]
                loop_start = int(loop_frame["start"])
                del blocks[loop_position:]
                active = bool(loop_frame["parent"])
                instruction_index = block_pairs[loop_start] + 1
                continue
            instruction_index += 1
            continue
        if operation == "end":
            start_index = block_pairs[instruction_index]
            if instructions[start_index].operation == "loop":
                if blocks and blocks[-1]["type"] == "loop" and blocks[-1]["start"] == start_index:
                    active = bool(blocks[-1]["parent"])
                    instruction_index = start_index + 1
                    continue
            elif blocks and blocks[-1]["type"] == "if":
                frame = blocks.pop()
                active = bool(frame["parent"])
            instruction_index += 1
            continue
        if not active:
            instruction_index += 1
            continue
        if operation == "return":
            return int(arguments[0]) if arguments else 0
        action = arguments[0]
        expanded = [_expand(value, variables, instruction.line) for value in arguments[1:]]
        try:
            last_status, result = action_runner(action, expanded, variables, host_app)
            if last_status:
                variables["last_error"] = result
        except ScriptError as error:
            raise ScriptError(f"Zeile {instruction.line}: {error}") from error
        instruction_index += 1
    return last_status


def run_module(module_dir: str, module_id: str, host_app: str) -> int:
    if __package__:
        from .runtime import ModuleError, resolve_module
    else:
        from runtime import ModuleError, resolve_module

    try:
        module = resolve_module(module_dir, module_id)
        if module.backend != "dsl" or module.entry is None:
            raise ModuleError(f"{module_id} ist kein DSL-Modul.")
        path = Path(module_dir, module.entry)
        source = path.read_text(encoding="utf-8")
        return run_script(source, host_app)
    except (ModuleError, OSError, UnicodeError, ScriptError) as error:
        print(f"Modulskript konnte nicht ausgeführt werden: {error}", file=__import__("sys").stderr)
        return 1
