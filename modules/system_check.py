#!/usr/bin/env python3

from __future__ import annotations

import os
import signal
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


def run_command(arguments: list[str]) -> str:
    result = subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip()


class SystemCheck:
    def __init__(self) -> None:
        self.window = Gtk.Window(title="VeloOS · System-Check")
        self.window.set_default_size(1040, 680)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.set_border_width(16)
        self.window.connect("destroy", Gtk.main_quit)

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.window.add(layout)
        self.summary = Gtk.Label(xalign=0)
        self.summary.set_line_wrap(True)
        layout.pack_start(self.summary, False, False, 0)

        self.model = Gtk.ListStore(int, str, str, str, str, str, str)
        self.table = Gtk.TreeView(model=self.model)
        self.table.set_grid_lines(Gtk.TreeViewGridLines.BOTH)
        for index, name in enumerate(
            ("PID", "Benutzer", "CPU", "RAM", "Speicher", "Status", "Prozess")
        ):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(name, renderer, text=index)
            column.set_resizable(True)
            self.table.append_column(column)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.table)
        layout.pack_start(scroller, True, True, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        layout.pack_start(actions, False, False, 0)
        refresh = Gtk.Button(label="Aktualisieren")
        refresh.connect("clicked", self.refresh)
        actions.pack_start(refresh, False, False, 0)
        terminate = Gtk.Button(label="Prozess beenden")
        terminate.connect("clicked", self.terminate_selected)
        actions.pack_start(terminate, False, False, 0)
        close = Gtk.Button(label="Schließen")
        close.connect("clicked", lambda *_: Gtk.main_quit())
        actions.pack_end(close, False, False, 0)

        self.refresh()
        self.window.show_all()

    def show_error(self, message: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text="System-Check",
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def refresh(self, *_args: object) -> None:
        try:
            uptime = run_command(["uptime", "-p"])
            memory = run_command(["free", "-h"])
            disk = run_command(["df", "-hP", os.path.expanduser("~")])
            processes = run_command(
                [
                    "ps",
                    "-eo",
                    "pid=,user=,pcpu=,pmem=,rss=,stat=,comm=",
                    "--sort=-pcpu",
                ]
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.show_error(f"Systeminformationen konnten nicht gelesen werden: {error}")
            return

        memory_values = next(
            (line.split() for line in memory.splitlines() if line.startswith("Mem:")),
            [],
        )
        swap_values = next(
            (line.split() for line in memory.splitlines() if line.startswith("Swap:")),
            [],
        )
        disk_lines = disk.splitlines()
        disk_usage = disk_lines[1].split() if len(disk_lines) > 1 else []
        memory_text = (
            f"{memory_values[2]} / {memory_values[1]}"
            if len(memory_values) > 2
            else "Nicht verfügbar"
        )
        swap_text = (
            f"{swap_values[2]} / {swap_values[1]}"
            if len(swap_values) > 2
            else "Nicht verfügbar"
        )
        disk_text = (
            f"{disk_usage[2]} von {disk_usage[1]} belegt ({disk_usage[4]})"
            if len(disk_usage) > 4
            else "Nicht verfügbar"
        )
        self.summary.set_text(
            f"Arbeitsspeicher: {memory_text}    ·    Swap: {swap_text}\n"
            f"Systemlaufwerk: {disk_text}    ·    Laufzeit: {uptime}"
        )
        self.model.clear()
        for line in processes.splitlines()[:20]:
            fields = line.split(None, 6)
            if len(fields) != 7:
                continue
            pid, user, cpu, ram, rss, state, command = fields
            self.model.append(
                [
                    int(pid),
                    user,
                    f"{cpu}%",
                    f"{ram}%",
                    f"{int(rss) / 1024:.0f} MB",
                    state,
                    command,
                ]
            )

    def terminate_selected(self, *_args: object) -> None:
        _model, iterator = self.table.get_selection().get_selected()
        if iterator is None:
            self.show_error("Wähle zuerst einen Prozess aus.")
            return
        pid = self.model[iterator][0]
        if pid <= 1 or pid in {os.getpid(), os.getppid()}:
            self.show_error("Dieser Systemprozess darf nicht beendet werden.")
            return
        try:
            owner = run_command(["ps", "-o", "uid=", "-p", str(pid)]).strip()
            if owner != str(os.getuid()):
                self.show_error("Es können nur eigene Benutzerprozesse beendet werden.")
                return
        except (OSError, subprocess.SubprocessError) as error:
            self.show_error(f"Der Prozessbesitzer konnte nicht geprüft werden: {error}")
            return

        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Prozess {pid} beenden?",
        )
        dialog.format_secondary_text(
            "Der Prozess erhält SIGTERM. Nicht gespeicherte Arbeit kann verloren gehen."
        )
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.YES:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as error:
            self.show_error(f"SIGTERM konnte nicht gesendet werden: {error}")
            return
        self.refresh()


if __name__ == "__main__":
    try:
        SystemCheck()
        Gtk.main()
    except (OSError, subprocess.SubprocessError) as error:
        print(f"System-Check konnte nicht gestartet werden: {error}", file=sys.stderr)
        raise SystemExit(1) from error
