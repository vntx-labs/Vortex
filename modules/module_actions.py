"""Narrow host actions for the DSL-backed file and server workflows."""

from __future__ import annotations

import fnmatch
import html
import os
import re
import signal
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


def _dialog(arguments: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["yad", "--center", "--on-top", "--modal", *arguments],
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
    )


def _pick(host_app: str, kind: str, prompt: str) -> str:
    result = subprocess.run(
        ["bash", host_app, "--module-api", "pick_path", kind, prompt],
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _message(title: str, text: str, kind: str = "info") -> None:
    _dialog([f"--{kind}", f"--title={title}", f"--text={html.escape(text)}", "--width=620"])


def _list(title: str, description: str, rows: list[list[str]], *, buttons: list[str] | None = None) -> str:
    command = [
        "--list", f"--title={title}", f"--text={html.escape(description)}",
        "--column=Aktion", "--column=Beschreibung", "--print-column=1", "--separator=",
        "--width=760", "--height=420",
    ]
    command.extend(f"--button={button}" for button in (buttons or ["Weiter:0", "Abbrechen:1"]))
    result = _dialog([*command, *(value for row in rows for value in row)])
    return result.stdout.strip() if result.returncode == 0 else ""


def _confirm(title: str, text: str) -> bool:
    return _dialog(["--question", f"--title={title}", f"--text={html.escape(text)}", "--width=680"]).returncode == 0


def _text_file(title: str, path: str, buttons: list[str] | None = None) -> int:
    result = _dialog([
        "--text-info", f"--title={title}", f"--filename={path}", "--width=820", "--height=520",
        *(f"--button={button}" for button in (buttons or ["Schließen:1"])),
    ])
    return result.returncode


def _entry(title: str, prompt: str, default: str = "") -> str:
    result = _dialog([
        "--entry", f"--title={title}", f"--text={html.escape(prompt)}",
        f"--entry-text={default}", "--width=620",
    ])
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def _safe_entries(root: str, mask: str) -> list[str]:
    entries: list[str] = []
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = [
            name for name in directories
            if not os.path.islink(os.path.join(current, name))
        ]
        entries.extend(
            os.path.join(current, name)
            for name in files
            if not os.path.islink(os.path.join(current, name))
            and fnmatch.fnmatch(name, mask)
        )
        if len(entries) > 2000:
            raise ValueError("Mehr als 2000 Dateien gefunden. Bitte die Dateimaske eingrenzen.")
    return entries


@contextmanager
def _bounded_file_operation():
    previous_handler = signal.getsignal(signal.SIGALRM)

    def timeout_handler(*_):
        raise TimeoutError("Die Dateiverarbeitung hat das Zeitlimit von 6 Sekunden überschritten.")

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(6)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def _replace_plan(mode: str, root: str, mask: str, pattern: str, replacement: str):
    with _bounded_file_operation():
        expression = re.compile(pattern)
        entries = _safe_entries(root, mask)
        changes: list[tuple[str, str]] = []
        total_bytes = 0
        for path in entries:
            if mode == "Dateinamen ersetzen":
                name = expression.sub(replacement, os.path.basename(path))
                if name in {"", ".", ".."} or "/" in name or "\0" in name:
                    raise ValueError(f"Unsicherer Zielname wird abgelehnt: {name!r}")
                if name != os.path.basename(path):
                    changes.append((path, name))
            else:
                try:
                    size = os.path.getsize(path)
                    if size > 4 * 1024 * 1024:
                        continue
                    total_bytes += size
                    if total_bytes > 64 * 1024 * 1024:
                        raise ValueError("Textverarbeitung auf 64 MB pro Lauf begrenzt.")
                    with open(path, encoding="utf-8", newline="") as source:
                        text = source.read()
                    updated = expression.sub(replacement, text)
                    if updated != text:
                        changes.append((path, updated))
                except (UnicodeError, OSError):
                    continue
        if mode == "Dateinamen ersetzen":
            targets = [os.path.join(os.path.dirname(path), name) for path, name in changes]
            changing = {path for path, _ in changes}
            if len(set(targets)) != len(targets) or any(
                os.path.exists(target) and target not in changing for target in targets
            ):
                raise ValueError("Vorschau abgebrochen: mindestens zwei Ziele würden kollidieren.")
        return entries, changes


def _apply_replacements(mode: str, changes: list[tuple[str, str]]) -> None:
    with _bounded_file_operation():
        if mode == "Dateinamen ersetzen":
            destinations = [os.path.join(os.path.dirname(path), name) for path, name in changes]
            changing = {path for path, _ in changes}
            if len(set(destinations)) != len(destinations) or any(
                os.path.exists(path) and path not in changing for path in destinations
            ):
                raise ValueError("Dateien wurden seit der Vorschau geändert; Zielkonflikt verhindert.")
            staged: list[tuple[str, str, str]] = []
            committed: list[tuple[str, str]] = []
            try:
                for index, (old, name) in enumerate(changes):
                    fd, temporary = tempfile.mkstemp(
                        prefix=f".velos-rename-{os.getpid()}-{index}-",
                        dir=os.path.dirname(old),
                    )
                    os.close(fd)
                    destination = os.path.join(os.path.dirname(old), name)
                    os.replace(old, temporary)
                    staged.append((old, temporary, destination))
                for old, temporary, destination in staged:
                    os.rename(temporary, destination)
                    committed.append((old, destination))
            except OSError:
                for old, destination in reversed(committed):
                    if os.path.exists(destination):
                        try:
                            os.rename(destination, old)
                        except OSError:
                            pass
                for old, temporary, _ in reversed(staged):
                    if os.path.exists(temporary):
                        try:
                            os.rename(temporary, old)
                        except OSError:
                            pass
                raise
            return
        for path, updated in changes:
            backup = path + ".velos-backup"
            if not os.path.exists(backup):
                shutil.copy2(path, backup)
            descriptor, temporary = tempfile.mkstemp(
                prefix=".velos-replace-", dir=os.path.dirname(path)
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
                    output.write(updated)
                shutil.copystat(path, temporary)
                os.replace(temporary, path)
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise


def _bulk_replace(host_app: str) -> None:
    root = _pick(host_app, "directory", "Ordner für Massen-Ersetzen wählen")
    if not root:
        return
    mode = _list(
        "Regex-Massen-Ersetzen",
        "Normale Dateien im gewählten Ordner und in Unterordnern werden verarbeitet.",
        [
            ["Dateiinhalte ersetzen", "Textinhalte anhand eines regulären Ausdrucks ändern"],
            ["Dateinamen ersetzen", "Dateien anhand eines Musters umbenennen"],
        ],
    )
    if not mode:
        return
    mask = _entry("Dateiauswahl", "Dateimaske (z. B. *.txt; keine Unterordner):", "*.txt")
    if not mask:
        return
    pattern = _entry("Suchmuster", "Regulärer Ausdruck (Python-Regex):")
    if not pattern:
        return
    replacement = _entry(
        "Ersetzen durch", r"Ersetzung; Gruppen wie \1 oder \g<name> sind möglich:"
    )
    try:
        entries, changes = _replace_plan(mode, root, mask, pattern, replacement)
    except (OSError, ValueError, re.error) as error:
        _message("Vorschau fehlgeschlagen", str(error), "error")
        return
    descriptor, preview = tempfile.mkstemp(prefix="velos-replace-", suffix=".txt")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(f"Modus: {mode}\nGeprüft: {len(entries)} Dateien\nÄnderungen: {len(changes)}\n\n")
            for path, value in changes[:100]:
                if mode == "Dateinamen ersetzen":
                    output.write(f"{os.path.basename(path)}  →  {value}\n")
                else:
                    output.write(f"{os.path.basename(path)}  ({len(value)} Zeichen nach Änderung)\n")
            if len(changes) > 100:
                output.write(f"\n… und {len(changes) - 100} weitere Änderungen\n")
            if not changes:
                output.write("Keine passenden Änderungen gefunden.\n")
        if _text_file(
            "Änderungsvorschau",
            preview,
            ["Schließen:1", "Änderungen anwenden:0"],
        ) != 0:
            return
        if not _confirm(
            "Änderungen anwenden?",
            "Die Änderungen sind nicht automatisch rückgängig zu machen. "
            "Bei Inhaltsänderungen werden .velos-backup-Dateien angelegt.",
        ):
            return
        _, fresh_changes = _replace_plan(mode, root, mask, pattern, replacement)
        _apply_replacements(mode, fresh_changes)
        _message("Massen-Ersetzen abgeschlossen", "Die Änderungen wurden angewendet.")
    except (OSError, ValueError, re.error) as error:
        _message("Ersetzen fehlgeschlagen", f"{error}\nBereits erstellte Backups behalten und Ordnerinhalt prüfen.", "error")
    finally:
        try:
            os.unlink(preview)
        except OSError:
            pass


def _converter(host_app: str) -> None:
    source = _pick(host_app, "file", "Datei für die Konvertierung auswählen")
    if not source:
        return
    kind = _list(
        "Datei-Konverter", "Wähle das Zielformat.",
        [["Bild → JPEG", "Bild konvertieren und auf 1920 × 1920 Pixel begrenzen"],
         ["Bild → PNG", "Bild konvertieren und auf 1920 × 1920 Pixel begrenzen"],
         ["Video → GIF", "Die ersten 15 Sekunden als GIF exportieren"]],
    )
    if not kind:
        return
    output_dir = _pick(host_app, "directory", "Zielordner für die konvertierte Datei")
    if not output_dir:
        return
    base = os.path.splitext(os.path.basename(source))[0]
    if kind in {"Bild → JPEG", "Bild → PNG"}:
        converter = shutil.which("magick") or shutil.which("convert")
        package, command = "imagemagick", "convert"
        extension = "jpg" if kind == "Bild → JPEG" else "png"
    else:
        converter = shutil.which("ffmpeg")
        package, command = "ffmpeg", "ffmpeg"
        extension = "gif"
    if not converter:
        result = subprocess.run(
            ["bash", host_app, "--module-api", "ensure_optional_tool", command,
             "Bild-/Videokonvertierung", package],
            check=False,
        )
        if result.returncode:
            return
        converter = shutil.which("magick") or shutil.which("convert") if package == "imagemagick" else shutil.which("ffmpeg")
    if not converter:
        _message("Konverter fehlt", "Der benötigte Konverter ist weiterhin nicht verfügbar.", "error")
        return
    output = os.path.join(output_dir, f"{base}.{extension}")
    if os.path.realpath(source) == os.path.realpath(output):
        _message("Ungültiges Ziel", "Eingabe und Ausgabe wären dieselbe Datei.", "error")
        return
    if os.path.exists(output) and not _confirm("Datei überschreiben?", f"{output} existiert bereits. Ersetzen?"):
        return
    if package == "imagemagick":
        arguments = [
            converter, "-limit", "memory", "128MiB", "-limit", "map", "256MiB",
            source, "-resize", "1920x1920>", "-strip", "-quality", "85", output,
        ]
        timeout_seconds = 45
    else:
        arguments = [
            converter, "-hide_banner", "-loglevel", "error", "-threads", "1", "-y",
            "-i", source, "-t", "15", "-vf",
            "fps=10,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
            output,
        ]
        timeout_seconds = 60
    try:
        completed = subprocess.run(
            ["nice", "-n", "10", *arguments],
            check=False, capture_output=True, text=True, timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as error:
        completed = None
        detail = str(error)
    else:
        detail = completed.stderr.strip()
    if completed is not None and completed.returncode == 0:
        _message("Konvertierung abgeschlossen", f"Gespeichert:\n{output}")
    else:
        try:
            os.unlink(output)
        except OSError:
            pass
        _message("Konvertierung fehlgeschlagen", detail or "Der Konverter konnte die Datei nicht umwandeln.", "error")


def _gpg_run(arguments: list[str], timeout_seconds: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["nice", "-n", "10", "timeout", "--kill-after=3s", f"{timeout_seconds}s", "gpg", *arguments],
        check=False, capture_output=True, text=True, timeout=timeout_seconds + 5,
    )


def _tree_limits(root: str) -> tuple[int, int]:
    size = count = 0
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if not os.path.islink(os.path.join(current, name))]
        for name in files:
            path = os.path.join(current, name)
            if os.path.islink(path):
                continue
            size += os.path.getsize(path)
            count += 1
            if count > 10000 or size > 256 * 1024 * 1024:
                raise ValueError("Safe-Limit überschritten: maximal 10000 Dateien und 256 MB.")
    return size, count


def _extract_safe_archive(archive_path: str, destination: str, name: str, folder_only: bool) -> str:
    if not tarfile.is_tarfile(archive_path):
        if folder_only:
            raise ValueError("Diese verschlüsselte Datei enthält kein unterstütztes Ordnerarchiv.")
        target = os.path.join(destination, name)
        if os.path.lexists(target):
            raise FileExistsError(f"Ziel existiert bereits: {target}")
        os.replace(archive_path, target)
        return target
    folder = name
    for suffix in (".tar.gz", ".tgz", ".tar"):
        if folder.endswith(suffix):
            folder = folder[:-len(suffix)]
            break
    target = os.path.join(destination, folder)
    if os.path.lexists(target):
        raise FileExistsError(f"Ziel existiert bereits: {target}")
    os.mkdir(target, 0o700)
    try:
        total = 0
        with tarfile.open(archive_path, "r:*") as archive:
            members = archive.getmembers()
            if not members or len(members) > 10000:
                raise ValueError("Archiv ist leer oder enthält zu viele Einträge (maximal 10000).")
            paths = [PurePosixPath(member.name) for member in members]
            roots = {path.parts[0] for path in paths if path.parts}
            strip_root = None
            if len(roots) == 1:
                candidate = next(iter(roots))
                if any(member.isdir() and path.parts == (candidate,) for member, path in zip(members, paths)):
                    strip_root = candidate
            root_path = os.path.realpath(target)
            for member, path in zip(members, paths):
                if path.is_absolute() or ".." in path.parts or not (member.isdir() or member.isfile()):
                    raise ValueError("Archiv enthält ungültige Pfade, Links oder Spezialdateien.")
                parts = path.parts
                if strip_root and parts and parts[0] == strip_root:
                    parts = parts[1:]
                    if not parts and member.isdir():
                        continue
                if not parts:
                    raise ValueError("Archiv enthält einen ungültigen leeren Dateipfad.")
                target_path = os.path.realpath(os.path.join(root_path, *parts))
                if os.path.commonpath((root_path, target_path)) != root_path:
                    raise ValueError("Archivpfad verlässt den Zielordner.")
                if member.isfile():
                    total += member.size
                    if total > 256 * 1024 * 1024:
                        raise ValueError("Archiv überschreitet das Entpacklimit von 256 MB.")
                if member.isdir():
                    os.makedirs(target_path, mode=0o700, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target_path), mode=0o700, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"Archivdatei nicht lesbar: {member.name}")
                copied = 0
                with source, open(target_path, "xb") as output:
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > member.size:
                            raise ValueError("Archivdatei überschreitet die deklarierte Größe.")
                        output.write(chunk)
                if copied != member.size:
                    raise ValueError(f"Archivdatei unvollständig: {member.name}")
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def local_safe_manage(host_app: str, action: str) -> None:
    available = subprocess.run(
        ["bash", host_app, "--module-api", "ensure_optional_tool", "gpg", "GnuPG", "gnupg"],
        check=False,
    )
    if available.returncode:
        return
    try:
        if action == "Datei verschlüsseln":
            source = _pick(host_app, "file", "Datei zum Verschlüsseln auswählen")
            if not source or os.path.getsize(source) > 256 * 1024 * 1024:
                if source:
                    raise ValueError("Dateien über 256 MB werden nicht verarbeitet.")
                return
            output = source + ".gpg"
            if os.path.lexists(output):
                raise FileExistsError(f"Zieldatei existiert bereits: {output}")
            result = _gpg_run(["--symmetric", "--cipher-algo", "AES256", "--output", output, source])
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "Verschlüsselung fehlgeschlagen oder Zeitlimit überschritten.")
            _message("Verschlüsselt", f"Verschlüsselte Datei erstellt:\n{output}\n\nDas Original bleibt unverändert.")
        elif action == "Ordner verschlüsseln":
            source = _pick(host_app, "directory", "Ordner zum Verschlüsseln auswählen")
            if not source:
                return
            destination = _pick(host_app, "directory", "Zielordner für den verschlüsselten Safe")
            if not destination:
                return
            source = str(Path(source).resolve(strict=True))
            destination = str(Path(destination).resolve(strict=True))
            if destination == source or source in Path(destination).parents:
                raise ValueError("Der Zielordner für den Safe darf nicht im Quellordner liegen.")
            _tree_limits(source)
            name = os.path.basename(source)
            output = os.path.join(destination, name + ".tar.gz.gpg")
            if os.path.lexists(output):
                raise FileExistsError(f"Zieldatei existiert bereits: {output}")
            archive = subprocess.Popen(
                ["tar", "-C", os.path.dirname(source), "-czf", "-", "--", name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                encrypt = subprocess.run(
                    ["nice", "-n", "10", "timeout", "--kill-after=3s", "120s", "gpg",
                     "--symmetric", "--cipher-algo", "AES256", "--output", output],
                    stdin=archive.stdout, check=False, capture_output=True, text=True, timeout=125,
                )
            finally:
                if archive.stdout:
                    archive.stdout.close()
            tar_error = archive.communicate()[1].decode("utf-8", "replace")
            if encrypt.returncode or archive.returncode:
                os.unlink(output) if os.path.exists(output) else None
                raise RuntimeError(encrypt.stderr.strip() or tar_error.strip() or "Ordner konnte nicht verschlüsselt werden.")
            _message("Safe erstellt", f"Verschlüsseltes Archiv erstellt:\n{output}\n\nDas Original bleibt unverändert.")
        elif action in {"Datei entschlüsseln", "Ordner-Safe entschlüsseln"}:
            source = _pick(host_app, "file", "GPG-Datei zum Entschlüsseln auswählen")
            if not source:
                return
            if os.path.getsize(source) > 256 * 1024 * 1024:
                raise ValueError("GPG-Dateien über 256 MB werden nicht verarbeitet.")
            destination = _pick(host_app, "directory", "Zielordner für die entschlüsselte Datei")
            if not destination:
                return
            descriptor, temp = tempfile.mkstemp(prefix=".velos-decrypt.", dir=destination)
            os.close(descriptor)
            try:
                result = _gpg_run(["--yes", "--output", temp, "--decrypt", source])
                if result.returncode:
                    raise RuntimeError("Entschlüsselung fehlgeschlagen. Passwort, GPG-Datei und Pinentry prüfen.")
                if os.path.getsize(temp) > 256 * 1024 * 1024:
                    raise ValueError("Entschlüsselte Ausgabe überschreitet 256 MB.")
                name = os.path.basename(source[:-4] if source.endswith(".gpg") else source)
                restored = _extract_safe_archive(
                    temp, destination, name, action == "Ordner-Safe entschlüsseln"
                )
                _message("Entschlüsselt", f"Datei oder Ordner wurde wiederhergestellt:\n{restored}")
            finally:
                try:
                    os.unlink(temp)
                except FileNotFoundError:
                    pass
    except (OSError, ValueError, RuntimeError, tarfile.TarError) as error:
        _message("Lokaler Safe", str(error), "error")


def _server_state_dir() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(Path.home(), ".cache")
    return os.path.join(base, "velos-dev-server")


_server_processes: dict[int, subprocess.Popen[bytes]] = {}


def _owned_server_pid(port: int) -> int | None:
    pid_file = os.path.join(_server_state_dir(), f"server-{port}.pid")
    try:
        pid = int(Path(pid_file).read_text(encoding="ascii").strip())
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        return pid if "VELO_DEV_SERVER" in command and f" {port} " in command else None
    except (OSError, ValueError):
        return None


def _port_free(port: int) -> bool:
    try:
        with socket.socket() as server:
            server.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _stop_owned_server(port: int, expected: int | None = None) -> bool:
    pid = _owned_server_pid(port)
    pid_file = os.path.join(_server_state_dir(), f"server-{port}.pid")
    if pid is None:
        try:
            os.unlink(pid_file)
        except OSError:
            pass
        return True
    if expected is not None and pid != expected:
        return True
    try:
        os.kill(pid, signal.SIGTERM)
        child = _server_processes.get(pid)
        if child is not None:
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                return False
            _server_processes.pop(pid, None)
            try:
                os.unlink(pid_file)
            except OSError:
                pass
            return True
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            return False
        os.unlink(pid_file)
        return True
    except OSError:
        return False


def _server_child(port: int, directory: str, logfile: str) -> None:
    from http.server import HTTPServer, SimpleHTTPRequestHandler

    os.chdir(directory)

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/__velos_health":
                self.send_response(200)
                self.send_header("X-VeloOS-Server", "ready")
                self.end_headers()
                self.wfile.write(b"ready")
                return
            super().do_GET()

        def log_message(self, *_):
            pass

    with open(logfile, "a", encoding="utf-8") as output:
        os.dup2(output.fileno(), 1)
        os.dup2(output.fileno(), 2)
        HTTPServer(("127.0.0.1", port), Handler).serve_forever()


def _start_server(port: int, directory: str) -> int:
    state_dir = _server_state_dir()
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    os.chmod(state_dir, 0o700)
    logfile = os.path.join(state_dir, f"server-{port}.log")
    Path(logfile).write_text("", encoding="utf-8")
    os.chmod(logfile, 0o600)
    process = subprocess.Popen(
        [sys.executable, __file__, "--serve", "VELO_DEV_SERVER", str(port), directory, logfile],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _server_processes[process.pid] = process
    ready = False
    for _ in range(30):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2) as connection:
                connection.sendall(b"GET /__velos_health HTTP/1.0\r\nHost: localhost\r\n\r\n")
                if b"X-VeloOS-Server: ready" in connection.recv(4096):
                    ready = True
                    break
        except OSError:
            time.sleep(0.1)
    if not ready:
        process.terminate()
        process.wait(timeout=2)
        return 0
    pid_file = os.path.join(state_dir, f"server-{port}.pid")
    descriptor = os.open(pid_file, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as output:
        output.write(f"{process.pid}\n")
    return process.pid


def local_server_manage(host_app: str) -> None:
    directory = _pick(host_app, "directory", "Ordner für den lokalen Server wählen")
    if not directory:
        return
    port = 8080
    if not _port_free(port):
        owned = _owned_server_pid(port)
        if owned is not None:
            action = _list(
                "Port 8080 ist belegt",
                "Ein eigener VeloOS-Server läuft bereits. Du kannst ihn ersetzen oder einen weiteren Port verwenden.",
                [["🔄  Eigenen Server auf 8080 neu starten", "Den eigenen Server ersetzen"],
                 ["↗  Nächsten freien Port verwenden", "Den laufenden Server beibehalten"],
                 ["Abbrechen", "Nichts ändern"]],
                buttons=["Weiter:0", "Abbrechen:1"],
            )
            if action.startswith("🔄"):
                if not _stop_owned_server(port, owned):
                    _message("Serverfehler", "Der eigene Serverprozess auf Port 8080 konnte nicht beendet werden.", "error")
                    return
            elif action.startswith("↗"):
                port = next((candidate for candidate in range(8081, 8181) if _port_free(candidate)), 0)
            else:
                return
        else:
            action = _list(
                "Port 8080 ist belegt",
                "Port 8080 wird von einem anderen Programm verwendet. Vortex beendet keine fremden Prozesse.",
                [["↗  Nächsten freien Port verwenden", "Einen freien Port zwischen 8081 und 8180 verwenden"],
                 ["Abbrechen", "Nichts ändern"]],
            )
            if not action.startswith("↗"):
                return
            port = next((candidate for candidate in range(8081, 8181) if _port_free(candidate)), 0)
    if not port:
        _message("Kein freier Port", "Zwischen Port 8081 und 8180 wurde kein freier Port gefunden.", "error")
        return
    pid = _start_server(port, directory)
    if not pid:
        logfile = os.path.join(_server_state_dir(), f"server-{port}.log")
        _message("Serverfehler", f"Der Server konnte auf Port {port} nicht gestartet werden.\nProtokoll: {logfile}", "error")
        return
    url = f"http://127.0.0.1:{port}"
    if shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        _message("Lokaler Server läuft", f"Öffne diese Adresse im Browser:\n{url}")
    _dialog([
        "--info", "--title=Lokaler Server läuft",
        f"--text={html.escape('Dein lokaler Server ist bereit\\n\\nProjektordner: ' + directory + '\\n\\nLokale Adresse: ' + url + '\\n\\nDer Server wird geschlossen, wenn du dieses Fenster schließt.')}",
        "--width=560", "--height=300",
    ])
    if not _stop_owned_server(port, pid):
        _message("Server läuft noch", f"Der Server auf Port {port} läuft möglicherweise noch. PID: {pid}", "error")


def dispatch(action: str, host_app: str, arguments: list[str]) -> tuple[int, str]:
    os.environ["VORTEX_HOST_APP"] = host_app
    if action == "module.file_tools.replace" and not arguments:
        _bulk_replace(host_app)
    elif action == "module.file_tools.convert" and not arguments:
        _converter(host_app)
    elif action == "module.local_safe":
        if len(arguments) != 1:
            return 2, "module.local_safe erwartet die gewählte Aktion."
        local_safe_manage(host_app, arguments[0])
    elif action == "module.local_server" and not arguments:
        local_server_manage(host_app)
    else:
        return 2, f"Unbekannte Modulaktion {action!r}."
    return 0, ""


if (
    __name__ == "__main__"
    and len(sys.argv) == 6
    and sys.argv[1] == "--serve"
    and sys.argv[2] == "VELO_DEV_SERVER"
):
    _server_child(int(sys.argv[3]), sys.argv[4], sys.argv[5])
