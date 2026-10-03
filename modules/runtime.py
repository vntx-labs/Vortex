#!/usr/bin/env python3
"""Loader for Vortex's line-oriented .vmod module catalog."""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass


class ModuleError(ValueError):
    """Raised when a module definition is invalid."""


@dataclass(frozen=True)
class Module:
    module_id: str
    name: str
    description: str
    icon: str
    backend: str
    entry: str | None
    order: int


def load_modules(module_dir: str) -> list[Module]:
    root = os.path.realpath(module_dir)
    if not os.path.isdir(root):
        raise ModuleError(f"Modulordner nicht gefunden: {module_dir}")

    modules = []
    seen_ids = set()
    required = {"name", "description", "icon", "backend", "order"}
    allowed = required | {"entry"}
    try:
        definitions = sorted(
            os.path.join(root, entry)
            for entry in os.listdir(root)
            if entry.endswith(".vmod")
        )
    except OSError as error:
        raise ModuleError(f"Modulordner konnte nicht gelesen werden: {error}") from error

    for filename in definitions:
        real_filename = os.path.realpath(filename)
        if os.path.commonpath((root, real_filename)) != root:
            raise ModuleError(f"Moduldefinition verlässt den Modulordner: {filename}")
        try:
            with open(real_filename, encoding="utf-8") as source:
                lines = [
                    line.strip()
                    for line in source
                    if line.strip() and not line.lstrip().startswith("#")
                ]
        except (OSError, UnicodeError) as error:
            raise ModuleError(f"{filename}: {error}") from error

        if not lines or not lines[0].startswith("module "):
            raise ModuleError(f"{filename}: erste Zeile muss 'module <id>' sein")
        module_id_parts = lines[0].split()
        if len(module_id_parts) != 2:
            raise ModuleError(f"{filename}: Modul-ID fehlt oder ist ungültig")
        module_id = module_id_parts[1]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", module_id):
            raise ModuleError(f"{filename}: ungültige Modul-ID {module_id!r}")
        if module_id in seen_ids:
            raise ModuleError(f"{filename}: Modul-ID {module_id!r} ist doppelt")
        seen_ids.add(module_id)

        fields = {}
        for line in lines[1:]:
            key, separator, raw_value = line.partition(" ")
            if not separator or key not in allowed or key in fields:
                raise ModuleError(f"{filename}: ungültige oder doppelte Direktive {line!r}")
            try:
                value = shlex.split(raw_value)
            except ValueError as error:
                raise ModuleError(f"{filename}: ungültiger Wert für {key!r}") from error
            if len(value) != 1:
                raise ModuleError(f"{filename}: Wert für {key!r} muss genau ein Textwert sein")
            fields[key] = value[0]

        missing = required - fields.keys()
        if missing:
            raise ModuleError(
                f"{filename}: fehlende Direktiven: {', '.join(sorted(missing))}"
            )
        if fields["backend"] not in {"shell", "dsl", "python"}:
            raise ModuleError(f"{filename}: unbekanntes Backend {fields['backend']!r}")
        try:
            order = int(fields["order"])
        except ValueError as error:
            raise ModuleError(f"{filename}: order muss eine ganze Zahl sein") from error
        entry = fields.get("entry")
        if not entry or os.path.isabs(entry):
            raise ModuleError(f"{filename}: {fields['backend']}-Module benötigen einen relativen entry")
        entry_parts = entry.split("/")
        if any(
            part in {"", ".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
            for part in entry_parts
        ):
            raise ModuleError(f"{filename}: ungültiger Modulpfad {entry!r}")
        entry_path = os.path.realpath(os.path.join(root, entry))
        expected_suffix = {
            "python": ".py",
            "dsl": ".vscript",
            "shell": ".sh",
        }[fields["backend"]]
        if (
            os.path.commonpath((root, entry_path)) != root
            or not entry_path.endswith(expected_suffix)
            or not os.path.isfile(entry_path)
        ):
            raise ModuleError(f"{filename}: ungültige Moduldatei {entry!r}")
        entry = os.path.relpath(entry_path, root)
        modules.append(
            Module(
                module_id=module_id,
                name=fields["name"],
                description=fields["description"],
                icon=fields["icon"],
                backend=fields["backend"],
                entry=entry,
                order=order,
            )
        )

    if not modules:
        raise ModuleError(f"Keine .vmod-Module in {root} gefunden.")
    modules.sort(key=lambda module: (module.order, module.module_id))
    return modules


def resolve_module(module_dir: str, module_id: str) -> Module:
    for module in load_modules(module_dir):
        if module.module_id == module_id:
            return module
    raise ModuleError(f"Unbekanntes Modul: {module_id}")


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "run":
        if len(sys.argv) != 5:
            print(
                "Aufruf: runtime.py run MODULES_DIR MODULE_ID HOST_APP",
                file=sys.stderr,
            )
            return 2
        if __package__:
            from .language import run_module
        else:
            from language import run_module

        return run_module(sys.argv[2], sys.argv[3], sys.argv[4])
    if len(sys.argv) != 4 or sys.argv[1] != "resolve":
        print(
            "Aufruf: runtime.py resolve MODULES_DIR MODULE_ID | "
            "run MODULES_DIR MODULE_ID HOST_APP",
            file=sys.stderr,
        )
        return 2
    try:
        module = resolve_module(sys.argv[2], sys.argv[3])
    except ModuleError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"{module.backend}\t{module.entry or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
