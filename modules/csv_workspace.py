#!/usr/bin/env python3
"""YAD workflow for the GTK CSV table editor and related CSV operations."""

from __future__ import annotations

import csv
import decimal
import html
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
EDITOR = MODULE_DIR / "csv_table_editor.py"
HOST_APP = os.environ.get("VORTEX_HOST_APP", "")


def yad(arguments: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["yad", "--center", "--on-top", "--modal", *arguments],
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
    )


def info(title: str, text: str, error: bool = False) -> None:
    kind = "error" if error else "info"
    yad([f"--{kind}", f"--title={title}", f"--text={html.escape(text)}", "--width=620"])


def choose_path(kind: str, prompt: str) -> str:
    if not HOST_APP:
        return ""
    result = subprocess.run(
        ["bash", HOST_APP, "--module-api", "pick_path", kind, prompt],
        check=False, capture_output=True, text=True, timeout=600,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def choose(title: str, description: str, rows: list[list[str]], buttons=None) -> str:
    args = [
        "--list", f"--title={title}", f"--text={html.escape(description)}",
        "--column=Aktion", "--column=Beschreibung", "--print-column=1", "--separator=",
        "--width=740", "--height=420",
    ]
    args.extend(f"--button={button}" for button in (buttons or ["Weiter:0", "Abbrechen:1"]))
    result = yad([*args, *(cell for row in rows for cell in row)])
    return result.stdout.strip() if result.returncode == 0 else ""


def dialect_for(path: str):
    with open(path, encoding="utf-8-sig", newline="") as source:
        sample = source.read(8192)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def read_rows(path: str):
    dialect = dialect_for(path)
    with open(path, encoding="utf-8-sig", newline="") as source:
        return dialect, list(csv.reader(source, dialect))


def text_info(title: str, path: str, buttons: list[str] | None = None) -> int:
    result = yad([
        "--text-info", f"--title={title}", f"--filename={path}",
        "--width=980", "--height=620",
        *(f"--button={button}" for button in (buttons or ["Schließen:1"])),
    ])
    return result.returncode


def write_csv(path: str, dialect, rows) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as destination:
        writer = csv.writer(destination, dialect)
        writer.writerows(rows)


def search_csv(path: str, term: str, output: str) -> None:
    dialect, rows = read_rows(path)
    query = term.casefold()
    selected = rows[:1] + [
        row for row in rows[1:]
        if any(query in cell.casefold() for cell in row)
    ]
    write_csv(output, dialect, selected)


def sort_csv(path: str, column: str, descending: bool, output: str) -> None:
    dialect, rows = read_rows(path)
    if not rows:
        raise ValueError("Die CSV-Datei enthält keine Daten.")
    try:
        index = rows[0].index(column)
    except ValueError as error:
        raise ValueError("Die ausgewählte Spalte ist nicht mehr vorhanden.") from error

    def key(row):
        value = row[index] if index < len(row) else ""
        try:
            return 0, decimal.Decimal(value)
        except decimal.InvalidOperation:
            return 1, value.casefold()

    sorted_rows = sorted(rows[1:], key=key, reverse=descending)
    write_csv(output, dialect, [rows[0], *sorted_rows])


def show_csv_info(path: str) -> None:
    dialect, rows = read_rows(path)
    headers = rows[0] if rows else []
    text = (
        f"Datei: {os.path.basename(path)}\n"
        f"Größe: {os.path.getsize(path):,} Bytes\n"
        f"Datensätze (ohne Kopfzeile): {max(0, len(rows) - 1):,}\n"
        f"Spalten: {len(headers):,}\n"
        f"Trennzeichen: {dialect.delimiter!r}"
    )
    info("Dateiinformationen", text)


def initial_file() -> str:
    start = choose(
        "CSV Workspace",
        "Mit einer Tabelle starten. Eine neue Tabelle kann ohne vorgeschalteten Dateidialog geöffnet werden.",
        [["＋  Neue Tabelle", "Leere Tabelle direkt im Editor beginnen"],
         ["📂  Vorhandene CSV öffnen", "Eine CSV-Datei auswählen und bearbeiten"]],
    )
    if start.startswith("＋"):
        return "--new"
    if start.startswith("📂"):
        return choose_path("file", "CSV-Datei auswählen")
    return ""


def run() -> int:
    path = initial_file()
    if not path:
        return 0
    while path:
        if path == "--new" or os.path.isfile(path):
            result = subprocess.run(
                [sys.executable, str(EDITOR), path],
                check=False, capture_output=True, text=True,
            )
            if result.returncode:
                info("Tabellen-Editor", "Der Tabelleneditor konnte nicht gestartet oder die CSV-Datei nicht gespeichert werden.", True)
                if path != "--new" and not os.path.isfile(path):
                    path = ""
                    continue
            if result.stdout.strip():
                path = result.stdout.strip()
            elif path == "--new":
                path = ""
        else:
            info("CSV Workspace", "Die ausgewählte CSV-Datei kann nicht gelesen werden.", True)
            path = initial_file()
            continue
        while path and os.path.isfile(path):
            try:
                dialect = dialect_for(path)
            except (OSError, UnicodeError, csv.Error):
                info("CSV Workspace", "Die CSV-Datei konnte nicht gelesen werden.", True)
                break
            action = choose(
                f"CSV Workspace • {os.path.basename(path)}",
                f"<b>{html.escape(path)}</b>\nTrennzeichen: {dialect.delimiter!r}",
                [["✏️  Tabelle bearbeiten", "Zellen, Zeilen und Spalten ändern und speichern"],
                 ["🔍  Suchen", "Alle Spalten nach einem Begriff durchsuchen"],
                 ["↕️  Sortieren", "Nach einer Spalte auf- oder absteigend sortieren"],
                 ["ℹ️  Dateiinformationen", "Größe, Datensätze und Trennzeichen anzeigen"],
                 ["←  Zurück", "Eine andere Datei auswählen"]],
                ["Öffnen:0", "Zurück:1"],
            )
            if not action or action.startswith("←"):
                break
            try:
                if action.startswith("✏️"):
                    edited = subprocess.run(
                        [sys.executable, str(EDITOR), path],
                        check=False, capture_output=True, text=True,
                    )
                    if edited.returncode:
                        info("Tabellen-Editor", "Der Tabelleneditor konnte nicht gestartet oder die CSV-Datei nicht gespeichert werden.", True)
                    elif edited.stdout.strip():
                        path = edited.stdout.strip()
                elif action.startswith("🔍"):
                    result = yad([
                        "--entry", "--title=CSV durchsuchen",
                        "--text=Suchbegriff (ohne Beachtung der Groß-/Kleinschreibung):",
                        "--width=520",
                    ])
                    term = result.stdout.rstrip("\n") if result.returncode == 0 else ""
                    if term:
                        with tempfile.NamedTemporaryFile(prefix="velos-csv-search-", suffix=".csv", delete=False) as result_file:
                            result_path = result_file.name
                        try:
                            search_csv(path, term, result_path)
                            text_info(f"Suchergebnisse • {os.path.basename(path)}", result_path)
                        finally:
                            os.unlink(result_path)
                elif action.startswith("↕️"):
                    _, rows = read_rows(path)
                    if not rows or not rows[0]:
                        info("CSV Workspace", "Die CSV-Datei hat keine Kopfzeile.", True)
                        continue
                    column_result = yad([
                        "--list", "--title=Sortierspalte wählen", "--column=Spalte",
                        "--separator=", "--width=560", "--height=420",
                        "--button=Weiter:0", "--button=Abbrechen:1", *rows[0],
                    ])
                    column = column_result.stdout.strip() if column_result.returncode == 0 else ""
                    if not column:
                        continue
                    order = choose(
                        "Sortierreihenfolge", "Wähle die Sortierrichtung.",
                        [["Aufsteigend", "A bis Z bzw. kleinster bis größter Wert"],
                         ["Absteigend", "Z bis A bzw. größter bis kleinster Wert"]],
                    )
                    if not order:
                        continue
                    with tempfile.NamedTemporaryFile(prefix="velos-csv-sort-", suffix=".csv", delete=False) as result_file:
                        result_path = result_file.name
                    try:
                        sort_csv(path, column, order == "Absteigend", result_path)
                        response = text_info(
                            "Sortierte Vorschau", result_path,
                            ["Schließen:0", "Speichern unter…:2"],
                        )
                        if response == 2:
                            target_dir = choose_path("directory", "Zielordner für die sortierte CSV")
                            if target_dir:
                                stem = os.path.splitext(os.path.basename(path))[0]
                                name = yad([
                                    "--entry", "--title=Dateiname",
                                    "--text=Name für die sortierte CSV:",
                                    f"--entry-text={stem}_sortiert.csv",
                                ])
                                filename = name.stdout.rstrip("\n") if name.returncode == 0 else ""
                                if filename:
                                    target = os.path.join(target_dir, os.path.basename(filename))
                                    shutil.copyfile(result_path, target)
                                    info("Export abgeschlossen", f"Sortierte CSV gespeichert:\n{target}")
                    finally:
                        os.unlink(result_path)
                elif action.startswith("ℹ️"):
                    show_csv_info(path)
            except (OSError, UnicodeError, csv.Error, ValueError) as error:
                info("CSV Workspace", f"Die CSV-Datei konnte nicht verarbeitet werden:\n{error}", True)
        if path == "--new" or not os.path.isfile(path):
            path = initial_file()
        else:
            path = initial_file()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
