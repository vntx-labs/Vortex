#!/usr/bin/env python3
"""Offline WebKit builder and TXT/Markdown HTML export workflow."""

from __future__ import annotations

import base64
import html
import os
import shlex
import subprocess
import sys
from pathlib import Path

HOST_APP = os.environ.get("VORTEX_HOST_APP", "")
MODULE_DIR = Path(__file__).resolve().parent
BUILDER = MODULE_DIR / "web_studio_builder.html"
STYLES = {
    "Modern & Minimal": (
        "body{font:16px/1.7 system-ui,sans-serif;max-width:860px;margin:48px auto;padding:0 24px;"
        "color:#172033;background:#f6f8fc}h1,h2{line-height:1.2;color:#102a56}a{color:#2563eb}"
        "pre{padding:16px;overflow:auto;background:#e9eef7;border-radius:10px}"
    ),
    "Dark Tech": (
        "body{font:15px/1.7 ui-monospace,monospace;max-width:900px;margin:48px auto;padding:0 24px;"
        "color:#d1fae5;background:#0b1220}h1,h2{color:#6ee7b7}a{color:#7dd3fc}"
        "pre{padding:16px;overflow:auto;background:#111c2e;border-radius:10px}"
    ),
    "Corporate / Standard": (
        "body{font:16px/1.8 Georgia,serif;max-width:760px;margin:52px auto;padding:0 24px;"
        "color:#293548;background:#fff}h1,h2{font-family:system-ui,sans-serif;color:#15345b}"
        "a{color:#1d4ed8}pre{padding:16px;overflow:auto;background:#f1f5f9;border-radius:10px}"
    ),
}


def yad(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["yad", "--center", "--on-top", "--modal", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def message(title: str, text: str, error: bool = False) -> None:
    yad([
        "--error" if error else "--info", f"--title={title}",
        f"--text={html.escape(text)}", "--width=620",
    ])


def pick(kind: str, prompt: str) -> str:
    if not HOST_APP:
        return ""
    result = subprocess.run(
        ["bash", HOST_APP, "--module-api", "pick_path", kind, prompt],
        check=False, capture_output=True, text=True, timeout=600,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def choose(title: str, description: str, column: str, rows: list[str]) -> str:
    result = yad([
        "--list", f"--title={title}", f"--text={html.escape(description)}",
        f"--column={column}", "--separator=", "--width=620", "--height=340",
        "--button=Weiter:0", "--button=Abbrechen:1", *rows,
    ])
    return result.stdout.strip() if result.returncode == 0 else ""


def markdown_html(text: str) -> str:
    _ensure_python_feature("markdown")
    try:
        import markdown
    except ImportError as error:
        raise RuntimeError("Markdown-Konvertierung benötigt das Python-Paket 'markdown'.") from error
    return markdown.markdown(text, extensions=["fenced_code", "tables"])


def _ensure_python_feature(feature: str) -> bool:
    if not HOST_APP:
        return False
    result = subprocess.run(
        ["bash", HOST_APP, "--module-api", "ensure_python_feature", feature],
        check=False,
    )
    return result.returncode == 0



def text_html(text: str) -> str:
    return "".join(
        f"<p>{html.escape(paragraph.strip()).replace(chr(10), '<br>')}</p>"
        for paragraph in text.split("\n\n")
        if paragraph.strip()
    )


def make_document(title: str, body: str, template: str) -> str:
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        f"<title>{html.escape(title)}</title><style>{STYLES.get(template, STYLES['Modern & Minimal'])}"
        "</style></head><body>" + body + "</body></html>"
    )


def export_sources() -> None:
    selected = pick("text-multi", "TXT- und Markdown-Dateien auswählen")
    files = [path for path in selected.splitlines() if path]
    if not files:
        return
    template = choose(
        "Export-Design", "Wähle ein Design für deinen HTML-Export:",
        "Design", list(STYLES),
    )
    if not template:
        return
    strategy = "Einzelne Datei"
    if len(files) > 1:
        strategy = choose(
            "Mehrere Dateien", "Wie sollen die Dateien exportiert werden?",
            "Exportmodus",
            ["Separate HTML-Datei je Quelle", "Alle Inhalte in einer HTML-Datei zusammenführen"],
        )
        if not strategy:
            return
    output_dir = pick("directory", "Zielordner für den HTML-Export")
    if not output_dir:
        return
    try:
        sections = []
        for source in files:
            text = Path(source).read_text(encoding="utf-8")
            body = markdown_html(text) if source.lower().endswith(".md") else text_html(text)
            sections.append(
                f"<section><h1>{html.escape(os.path.basename(source))}</h1>{body}</section>"
            )
        if strategy == "Alle Inhalte in einer HTML-Datei zusammenführen":
            css = STYLES[template] + "section+section{border-top:1px solid #94a3b8;margin-top:3rem;padding-top:1rem}"
            document = (
                '<!doctype html><html lang="de"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width">'
                f"<title>Zusammengeführter Export</title><style>{css}</style></head><body>"
                + "".join(sections) + "</body></html>"
            )
            output = os.path.join(output_dir, "index.html")
            Path(output).write_text(document, encoding="utf-8")
        else:
            for source in files:
                text = Path(source).read_text(encoding="utf-8")
                body = markdown_html(text) if source.lower().endswith(".md") else text_html(text)
                base = os.path.splitext(os.path.basename(source))[0]
                Path(output_dir, base + ".html").write_text(
                    make_document(os.path.basename(source), body, template),
                    encoding="utf-8",
                )
        message("Export abgeschlossen", f"HTML-Dateien wurden gespeichert in:\n{output_dir}")
    except (OSError, UnicodeError, RuntimeError) as error:
        message("HTML-Export fehlgeschlagen", str(error), True)


def open_builder() -> None:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk
    except (ImportError, ValueError):
        if _yad_supports_html():
            _open_yad_builder()
            return
        if _ensure_python_feature("webkit2"):
            try:
                try:
                    gi.require_version("WebKit2", "4.1")
                except ValueError:
                    gi.require_version("WebKit2", "4.0")
                from gi.repository import WebKit2 as webkit
            except (ImportError, ValueError):
                pass
            else:
                WebKit2 = webkit
                _open_webkit_builder(Gdk, GLib, Gtk, WebKit2)
                return
        message(
            "WebKit-Unterstützung fehlt",
            "Für den visuellen Builder wird GTK 3/WebKit2 oder eine YAD-Version "
            "mit HTML-Unterstützung benötigt.",
            True,
        )
        return

    webkit = None
    try:
        try:
            gi.require_version("WebKit2", "4.1")
        except ValueError:
            gi.require_version("WebKit2", "4.0")
        from gi.repository import WebKit2 as webkit
    except (ImportError, ValueError):
        if _yad_supports_html():
            _open_yad_builder()
            return
        if _ensure_python_feature("webkit2"):
            try:
                try:
                    gi.require_version("WebKit2", "4.1")
                except ValueError:
                    gi.require_version("WebKit2", "4.0")
                from gi.repository import WebKit2 as webkit
            except (ImportError, ValueError):
                pass
            else:
                _open_webkit_builder(Gdk, GLib, Gtk, webkit)
                return
        message(
            "WebKit-Unterstützung fehlt",
            "WebKit2 oder eine YAD-Version mit HTML-Unterstützung ist nicht verfügbar.",
            True,
        )
        return
    WebKit2 = webkit
    _open_webkit_builder(Gdk, GLib, Gtk, WebKit2)


def _open_webkit_builder(Gdk, GLib, Gtk, WebKit2) -> None:

    Gtk.init(None)
    window = Gtk.Window(title="VeloOS Studio")
    window.set_default_size(1360, 900)
    window.set_position(Gtk.WindowPosition.CENTER)
    window.set_icon_name("applications-internet")
    header = Gtk.HeaderBar()
    header.set_show_close_button(True)
    title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    title = Gtk.Label()
    title.set_markup("<b>VeloOS Studio</b>")
    subtitle = Gtk.Label(label="Visueller Webseiten-Builder")
    subtitle.get_style_context().add_class("dim-label")
    title_box.pack_start(title, True, True, 0)
    title_box.pack_start(subtitle, True, True, 0)
    header.set_custom_title(title_box)
    window.set_titlebar(header)

    provider = Gtk.CssProvider()
    provider.load_from_data(
        b"window{background:#0b1220}headerbar{background:#111c2e;color:#e5edf8;"
        b"border:0;box-shadow:0 1px 0 #26364d;min-height:48px}"
    )
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    settings = WebKit2.Settings.new()
    settings.set_enable_javascript(True)
    settings.set_enable_developer_extras(False)
    for name, value in (
        ("set_enable_page_cache", False),
        ("set_enable_webgl", False),
        ("set_media_playback_requires_user_gesture", True),
    ):
        method = getattr(settings, name, None)
        if method is not None:
            method(value)
    web_view = WebKit2.WebView.new_with_settings(settings)

    def export(uri: str) -> None:
        prefix = "velos-export://save/"
        if not uri.startswith(prefix):
            message("Export fehlgeschlagen", "Ungültiger Export-Link.", True)
            return
        encoded = uri[len(prefix):]
        try:
            payload = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, base64.binascii.Error):
            message("Export fehlgeschlagen", "Der HTML-Inhalt ist ungültig.", True)
            return
        chooser = Gtk.FileChooserDialog(
            title="HTML exportieren",
            transient_for=window,
            action=Gtk.FileChooserAction.SAVE,
        )
        chooser.add_buttons(
            "Abbrechen", Gtk.ResponseType.CANCEL,
            "Speichern", Gtk.ResponseType.ACCEPT,
        )
        chooser.set_current_name("index.html")
        chooser.set_do_overwrite_confirmation(True)
        response = chooser.run()
        destination = chooser.get_filename() if response == Gtk.ResponseType.ACCEPT else None
        chooser.destroy()
        if not destination:
            return
        extension_added = not destination.lower().endswith(".html")
        if extension_added:
            destination += ".html"
        if extension_added and os.path.exists(destination):
            overwrite = Gtk.MessageDialog(
                transient_for=window,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Datei überschreiben?",
            )
            overwrite.format_secondary_text(destination)
            confirmed = overwrite.run() == Gtk.ResponseType.YES
            overwrite.destroy()
            if not confirmed:
                return
        if not destination.lower().endswith(".html"):
            destination += ".html"
        try:
            Path(destination).write_bytes(payload)
        except OSError as error:
            message("Export fehlgeschlagen", str(error), True)
        else:
            message("Export abgeschlossen", f"Deine Webseite wurde gespeichert:\n{destination}")

    def policy(_view, decision, decision_type):
        del _view
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith("velos-export://"):
                decision.ignore()
                export(uri)
                return True
        return False

    def load_failed(_view, _event, failing_uri, error):
        del _view, _event
        if failing_uri.startswith("file:"):
            message("Builder-Fehler", f"Der Builder-Inhalt konnte nicht geladen werden:\n{error}", True)
        return False

    web_view.connect("decide-policy", policy)
    web_view.connect("load-failed", load_failed)
    window.add(web_view)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    web_view.load_uri(GLib.filename_to_uri(str(BUILDER), None))
    Gtk.main()


def _yad_supports_html() -> bool:
    try:
        result = subprocess.run(
            ["yad", "--help-html"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "--html" in result.stdout + result.stderr


def _save_builder_uri(uri: str) -> None:
    prefix = "velos-export://save/"
    if not uri.startswith(prefix):
        message("Export fehlgeschlagen", "Ungültiger Export-Link.", True)
        return
    encoded = uri[len(prefix):]
    try:
        payload = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, base64.binascii.Error):
        message("Export fehlgeschlagen", "Der HTML-Inhalt ist ungültig.", True)
        return
    output_dir = pick("directory", "Exportordner für deine Webseite")
    if not output_dir:
        return
    result = yad([
        "--entry", "--title=HTML exportieren", "--text=Dateiname für deine Webseite:",
        "--entry-text=index.html", "--width=520",
    ])
    filename = result.stdout.strip() if result.returncode == 0 else ""
    if not filename:
        return
    filename = os.path.basename(filename)
    if not filename.lower().endswith(".html"):
        filename += ".html"
    destination = Path(output_dir, filename)
    if destination.exists():
        confirm = yad([
            "--question", "--title=Datei überschreiben?",
            f"--text={html.escape(str(destination))} existiert bereits. Soll die Datei ersetzt werden?",
            "--width=560",
        ])
        if confirm.returncode:
            return
    try:
        destination.write_bytes(payload)
    except OSError as error:
        message("Export fehlgeschlagen", str(error), True)
    else:
        message("Export abgeschlossen", f"Deine Webseite wurde gespeichert:\n{destination}")


def _open_yad_builder() -> None:
    handler = shlex.join([sys.executable, str(Path(__file__).resolve()), "--export-builder"])
    result = subprocess.run(
        [
            "yad", "--html", "--center", "--on-top", "--modal", "--browser",
            "--title=VeloOS · Visueller Web-Builder",
            f"--uri-handler={handler}", f"--uri={BUILDER.as_uri()}",
            "--width=1280", "--height=860",
        ],
        check=False,
    )
    if result.returncode:
        message("Web Studio", "YAD konnte den Web-Builder nicht öffnen. Prüfe die YAD-/WebKit-Installation.", True)


def run() -> int:
    if sys.argv[1:2] == ["--export-builder"]:
        if len(sys.argv) != 3:
            return 2
        _save_builder_uri(sys.argv[2])
        return 0
    while True:
        choice = choose(
            "Web Studio", "Gestalten und veröffentlichen",
            "Werkzeug",
            ["📄  TXT/MD exportieren", "🎨  Visueller Builder", "←  Zurück"],
        )
        if choice.startswith("📄"):
            export_sources()
        elif choice.startswith("🎨"):
            open_builder()
        else:
            return 0


if __name__ == "__main__":
    raise SystemExit(run())
