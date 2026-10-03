import base64
import concurrent.futures
import os
import pathlib
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import gi

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GtkSource", "4")
    from gi.repository import Gdk, GLib, Gtk, GtkSource
except (ImportError, ValueError) as error:
    print(f"Focus Code Editor konnte nicht gestartet werden: {error}", file=sys.stderr)
    raise SystemExit(2)

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_RUN_OUTPUT = 512 * 1024
MAX_RUN_SECONDS = 15
SNIPPET_STORE = os.path.join(os.path.expanduser("~"), ".local", "share", "velos", "snippets.tsv")
LANGUAGES = {
    "Automatisch": None, "Python": "python", "Bash": "sh", "JavaScript": "js",
    "Dash": "sh", "Ruby": "ruby", "PHP": "php", "Perl": "perl",
    "C": "c", "C++": "cpp", "Rust": "rust", "Lua": "lua",
    "HTML": "html", "CSS": "css", "JSON": "json", "Markdown": "markdown",
    "SQL": "sql", "Windows Batch (nicht ausführbar)": None,
    "Malbolge (nicht ausführbar)": None, "Klartext": "plain",
}
LANGUAGE_IDS = {value: key for key, value in LANGUAGES.items() if value}
LANGUAGE_IDS.update({"plain": "Klartext", "sh": "Bash"})
RUNNABLE_LANGUAGES = {
    "Python", "Bash", "Dash", "JavaScript", "Ruby", "PHP", "Perl",
    "C", "C++", "Rust", "Lua",
}
RUNTIME_COMMANDS = {
    "Bash": ("bash",), "Dash": ("dash",), "JavaScript": ("node", "nodejs"),
    "Ruby": ("ruby",), "PHP": ("php", "php83", "php82", "php81"), "Perl": ("perl",),
    "C": ("gcc",), "C++": ("g++",), "Rust": ("rustc",),
    "Lua": ("lua", "lua54", "lua53", "lua5.4", "lua5.3", "lua5.2", "lua5.1"),
}
RUNTIME_PACKAGES = {
    "apt-get": {
        "Bash": ("bash",), "Dash": ("dash",), "JavaScript": ("nodejs",),
        "Ruby": ("ruby",), "PHP": ("php-cli",), "Perl": ("perl",),
        "C": ("build-essential",), "C++": ("build-essential",),
        "Rust": ("rustc", "build-essential"), "Lua": ("lua5.4",),
    },
    "dnf": {
        "Bash": ("bash",), "Dash": ("dash",), "JavaScript": ("nodejs",),
        "Ruby": ("ruby",), "PHP": ("php-cli",), "Perl": ("perl",),
        "C": ("gcc",), "C++": ("gcc-c++",), "Rust": ("rust", "gcc"), "Lua": ("lua",),
    },
    "pacman": {
        "Bash": ("bash",), "Dash": ("dash",), "JavaScript": ("nodejs",),
        "Ruby": ("ruby",), "PHP": ("php",), "Perl": ("perl",),
        "C": ("base-devel",), "C++": ("base-devel",), "Rust": ("rust", "gcc"), "Lua": ("lua",),
    },
    "zypper": {
        "Bash": ("bash",), "Dash": ("dash",), "JavaScript": ("nodejs",),
        "Ruby": ("ruby",), "PHP": ("php8-cli",), "Perl": ("perl",),
        "C": ("gcc",), "C++": ("gcc-c++",), "Rust": ("rust", "gcc"), "Lua": ("lua54",),
    },
    "apk": {
        "Bash": ("bash",), "Dash": ("dash",), "JavaScript": ("nodejs",),
        "Ruby": ("ruby",), "PHP": ("php83",), "Perl": ("perl",),
        "C": ("build-base",), "C++": ("build-base",), "Rust": ("rust", "build-base"), "Lua": ("lua5.4",),
    },
}
EXTENSION_LANGUAGES = {
    ".py": "Python", ".sh": "Bash", ".bash": "Bash", ".dash": "Dash",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".rb": "Ruby", ".php": "PHP", ".pl": "Perl",
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".cxx": "C++",
    ".hpp": "C++", ".rs": "Rust", ".lua": "Lua",
    ".bat": "Windows Batch (nicht ausführbar)", ".cmd": "Windows Batch (nicht ausführbar)",
}

css = b"""
window.velos-editor { background: #111827; color: #e5edf8; }
window.velos-editor headerbar { background: #182235; border-bottom: 1px solid #2d3a50; }
window.velos-editor .editor-toolbar { background: #182235; }
window.velos-editor .editor-toolbar button, window.velos-editor button.editor-action {
    min-height: 34px; padding: 6px 11px; border: 1px solid #34445d; border-radius: 7px;
    background: #202e44; color: #e5edf8;
}
window.velos-editor .editor-toolbar button:hover, window.velos-editor button.editor-action:hover { background: #2b3d58; }
window.velos-editor .editor-toolbar button.suggested-action { background: #2563eb; border-color: #3976ed; }
window.velos-editor .editor-status { padding: 6px 12px; background: #182235; color: #95a6bf; font-size: 11px; }
window.velos-editor .search-bar { padding: 8px; background: #202e44; }
window.velos-editor .output-heading { padding: 7px 10px; background: #182235; color: #9db0ca; font-size: 11px; }
window.velos-editor textview { background: #111827; color: #e5edf8; }
window.velos-editor textview text { background: #111827; color: #e5edf8; }
window.velos-editor textview { font-family: monospace; font-size: 11pt; }
window.velos-editor .output-view { font-size: 10pt; }
window.velos-editor textview gutter { background: #151f30; color: #72829b; }
window.velos-editor notebook > header { background: #182235; }
window.velos-editor notebook tab { padding: 5px 9px; }
window.velos-editor entry, window.velos-editor combobox button {
    min-height: 32px; padding: 5px 9px; border: 1px solid #34445d; border-radius: 6px;
    background: #111827; color: #e5edf8;
}
window.velos-editor .picker-window { background: #f5f7fb; color: #182338; }
"""
provider = Gtk.CssProvider()
provider.load_from_data(css)
Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

window = Gtk.Window(title="Focus Code Editor · VeloOS")
window.get_style_context().add_class("velos-editor")
window.set_default_size(1180, 820)
window.set_position(Gtk.WindowPosition.CENTER)
window.set_modal(True)
window.set_keep_above(True)
window.connect("delete-event", lambda *_: close_editor())

state = {
    "documents": [], "untitled": 0, "running": False, "closing": False,
    "setting_language": False, "runtime_states": {},
}
runtime_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
manager = GtkSource.LanguageManager.get_default()
scheme_manager = GtkSource.StyleSchemeManager.get_default()

def current_document():
    page = notebook.get_current_page()
    if page < 0:
        return None
    return next((doc for doc in state["documents"] if doc["page"] == notebook.get_nth_page(page)), None)

def set_status(message=None):
    doc = current_document()
    if message is not None:
        status_message.set_text(message)
    if not doc:
        cursor_label.set_text("Keine Datei geöffnet")
        language_combo.set_active(0)
        save_button.set_sensitive(False)
        run_button.set_sensitive(False)
        return
    insert = doc["buffer"].get_iter_at_mark(doc["buffer"].get_insert())
    cursor_label.set_text(f"Zeile {insert.get_line() + 1} · Spalte {insert.get_line_offset() + 1} · UTF-8")
    selected = doc.get("run_language")
    if selected is None:
        language = doc["buffer"].get_language()
        selected = LANGUAGE_IDS.get(language.get_id()) if language else (
            "Automatisch" if not doc["path"] else "Klartext"
        )
    if selected not in LANGUAGES:
        selected = "Klartext"
    state["setting_language"] = True
    try:
        language_combo.set_active(list(LANGUAGES).index(selected))
    finally:
        state["setting_language"] = False
    save_button.set_sensitive(True)
    run_button.set_sensitive(not state["running"])

def update_tab(doc):
    label = doc["tab_label"]
    name = os.path.basename(doc["path"]) if doc["path"] else doc["name"]
    label.set_text(("● " if doc["dirty"] else "") + name)
    label.set_tooltip_text(doc["path"] or "Nicht gespeicherte Datei")
    set_status()

def set_dirty(doc, dirty=True):
    if doc["dirty"] == dirty:
        return
    doc["dirty"] = dirty
    update_tab(doc)

def set_language(doc, language_id=None):
    if language_id == "plain":
        language = None
    elif language_id:
        language = manager.get_language(language_id)
    elif doc["path"]:
        language = manager.guess_language(doc["path"], None)
    else:
        language = None
    doc["buffer"].set_language(language)
    doc["buffer"].set_highlight_syntax(language is not None)

def add_document(content="", path=None, name=None):
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        show_message("Diese Datei überschreitet das Editorlimit von 5 MB.")
        return None
    buffer = GtkSource.Buffer()
    buffer.set_max_undo_levels(500)
    buffer.set_highlight_matching_brackets(True)
    view = GtkSource.View.new_with_buffer(buffer)
    view.set_show_line_numbers(True)
    view.set_show_line_marks(True)
    view.set_highlight_current_line(True)
    view.set_auto_indent(True)
    view.set_insert_spaces_instead_of_tabs(True)
    view.set_indent_width(4)
    view.set_tab_width(4)
    view.set_smart_home_end(GtkSource.SmartHomeEndType.BEFORE)
    view.set_monospace(True)
    view.set_wrap_mode(Gtk.WrapMode.NONE)
    view.set_left_margin(12)
    view.set_right_margin(18)
    view.set_top_margin(10)
    completion = GtkSource.CompletionWords.new("Dieses Dokument", None)
    completion.register(buffer)
    view.get_completion().add_provider(completion)
    scheme = scheme_manager.get_scheme("oblivion") or scheme_manager.get_scheme("cobalt")
    if scheme:
        buffer.set_style_scheme(scheme)
    buffer.set_text(content)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.add(view)
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    page.pack_start(scroll, True, True, 0)
    tab_label = Gtk.Label()
    doc = {
        "page": page, "view": view, "buffer": buffer, "path": path,
        "name": name or "Unbenannt", "dirty": False, "tab_label": tab_label,
        "run_language": EXTENSION_LANGUAGES.get(pathlib.Path(path).suffix.lower()) if path else None,
    }
    state["documents"].append(doc)
    buffer.connect("changed", lambda _buffer, current=doc: set_dirty(current, True))
    buffer.connect("notify::cursor-position", lambda _buffer, _prop, current=doc: set_status())
    view.connect("key-press-event", on_editor_key)
    view.connect("focus-in-event", lambda *_: set_status())
    tab = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
    tab.pack_start(tab_label, False, False, 0)
    close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
    close.set_relief(Gtk.ReliefStyle.NONE)
    close.set_tooltip_text("Tab schließen")
    close.connect("clicked", lambda _button, current=doc: close_document(current))
    tab.pack_start(close, False, False, 0)
    page_index = notebook.append_page(page, tab)
    notebook.set_tab_reorderable(page, True)
    notebook.set_current_page(page_index)
    set_language(doc)
    update_tab(doc)
    if doc["run_language"]:
        ensure_runtime_async(doc["run_language"])
    page.show_all()
    view.grab_focus()
    return doc

def show_message(message, error=False):
    dialog = Gtk.MessageDialog(
        transient_for=window, modal=True,
        message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.CLOSE, text="Focus Code Editor",
    )
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()

def runtime_executable(language):
    if language == "Python":
        return sys.executable
    for name in RUNTIME_COMMANDS.get(language, ()):
        executable = shutil.which(name)
        if executable:
            return executable
    return None

def install_runtime(language):
    package_manager = os.environ.get("VELOOS_PACKAGE_MANAGER", "")
    packages = RUNTIME_PACKAGES.get(package_manager, {}).get(language)
    if not packages:
        raise RuntimeError(f"Für {language} gibt es auf diesem System keine unterstützte automatische Installation.")

    if os.geteuid() == 0:
        prefix = []
    elif os.environ.get("VELOOS_SUDO_SESSION") == "1" and shutil.which("sudo"):
        prefix = [shutil.which("sudo"), "-n", "--"]
    elif shutil.which("pkexec"):
        prefix = [shutil.which("pkexec")]
    else:
        raise RuntimeError("Keine aktive Administratorfreigabe für die Interpreter-Installation.")

    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    if package_manager == "apt-get":
        commands = [
            [*prefix, "apt-get", "update"],
            [*prefix, "apt-get", "install", "-y", "--", *packages],
        ]
    elif package_manager == "dnf":
        commands = [[*prefix, "dnf", "install", "-y", *packages]]
    elif package_manager == "pacman":
        commands = [[*prefix, "pacman", "-Sy", "--noconfirm", "--needed", *packages]]
    elif package_manager == "zypper":
        commands = [[*prefix, "zypper", "--non-interactive", "install", *packages]]
    elif package_manager == "apk":
        commands = [[*prefix, "apk", "add", "--no-progress", *packages]]
    else:
        raise RuntimeError("Kein unterstützter Paketmanager für die Interpreter-Installation gefunden.")

    for command in commands:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace",
            env=env, timeout=900, check=False,
        )
        if result.returncode:
            detail = result.stdout[-4000:].strip()
            raise RuntimeError(
                f"Installation von {language} fehlgeschlagen (Status {result.returncode})."
                + (f"\n\n{detail}" if detail else "")
            )
    if not runtime_executable(language):
        raise RuntimeError(f"Die Installation wurde beendet, aber der Interpreter für {language} ist nicht verfügbar.")

def finish_runtime_install(language, future):
    try:
        future.result()
        state["runtime_states"][language] = "ready"
        message = f"{language} ist bereit."
        failed = False
    except Exception as error:
        state["runtime_states"][language] = "error"
        message = f"Interpreter-Installation für {language} fehlgeschlagen:\n{error}"
        failed = True
    if current_document() and current_document().get("run_language") == language:
        set_status(message)
        if failed:
            show_message(message, True)
    return False

def ensure_runtime_async(language):
    if language not in RUNNABLE_LANGUAGES:
        return
    if runtime_executable(language):
        state["runtime_states"][language] = "ready"
        if current_document() and current_document().get("run_language") == language:
            set_status(f"{language} ist bereit.")
        return
    if state["runtime_states"].get(language) == "installing":
        return
    state["runtime_states"][language] = "installing"
    set_status(f"Installiere {language} im Hintergrund – du kannst weiter schreiben …")
    future = runtime_executor.submit(install_runtime, language)
    future.add_done_callback(lambda completed: GLib.idle_add(finish_runtime_install, language, completed))

def custom_file_dialog(save=False, initial=None):
    initial = os.path.abspath(initial or os.path.expanduser("~"))
    if os.path.isfile(initial):
        folder, filename = os.path.dirname(initial), os.path.basename(initial)
    else:
        folder, filename = initial, ""
    dialog = Gtk.Dialog(title="Speichern unter" if save else "Datei öffnen", transient_for=window, modal=True)
    dialog.set_default_size(880, 590)
    dialog.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
    dialog.add_button("Abbrechen", Gtk.ResponseType.CANCEL)
    accept = dialog.add_button("Speichern" if save else "Öffnen", 1001)
    accept.get_style_context().add_class("suggested-action")
    content = dialog.get_content_area()
    content.set_spacing(8)
    path_entry = Gtk.Entry()
    path_entry.set_text(folder)
    path_entry.set_placeholder_text("Ordnerpfad")
    path_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    path_bar.set_margin_start(8);path_bar.set_margin_end(8);path_bar.set_margin_top(8)
    up = Gtk.Button(label="↑")
    up.set_tooltip_text("Übergeordneten Ordner öffnen")
    up.connect("clicked", lambda *_: navigate_picker(os.path.dirname(picker_state["path"])))
    path_bar.pack_start(up, False, False, 0)
    path_bar.pack_start(path_entry, True, True, 0)
    content.pack_start(path_bar, False, False, 0)
    main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    content.pack_start(main, True, True, 0)
    places = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    places.set_size_request(150, -1)
    main.pack_start(places, False, False, 8)
    home = os.path.expanduser("~")
    def user_folder(kind, fallback):
        try:
            result = subprocess.run(["xdg-user-dir", kind], capture_output=True, text=True, timeout=1, check=False)
            return result.stdout.strip() if result.stdout.strip() and os.path.isdir(result.stdout.strip()) else fallback
        except (OSError, subprocess.TimeoutExpired):
            return fallback
    user_places = [
        ("Persönliche Dateien", home),
        ("Dokumente", user_folder("DOCUMENTS", os.path.join(home, "Documents"))),
        ("Schreibtisch", user_folder("DESKTOP", os.path.join(home, "Desktop"))),
        ("Downloads", user_folder("DOWNLOAD", os.path.join(home, "Downloads"))),
    ]
    for label, place in user_places:
        if os.path.isdir(place):
            button = Gtk.Button(label=label)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.connect("clicked", lambda _button, value=place: navigate_picker(value))
            places.pack_start(button, False, False, 0)
    store = Gtk.ListStore(str, str, str, bool)
    tree = Gtk.TreeView(model=store)
    tree.set_headers_visible(False)
    icon = Gtk.CellRendererPixbuf()
    column = Gtk.TreeViewColumn("Name", icon)
    column.add_attribute(icon, "icon-name", 0)
    text = Gtk.CellRendererText()
    column.pack_start(text, True)
    column.add_attribute(text, "text", 1)
    tree.append_column(column)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroller.add(tree)
    main.pack_start(scroller, True, True, 8)
    footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    content.pack_start(footer, False, False, 8)
    filename_entry = Gtk.Entry()
    filename_entry.set_placeholder_text("Dateiname")
    filename_entry.set_text(filename)
    if save:
        footer.pack_start(Gtk.Label(label="Dateiname:", xalign=0), False, False, 0)
        footer.pack_start(filename_entry, True, True, 0)
    search = Gtk.SearchEntry()
    search.set_placeholder_text("Im Ordner suchen")
    footer.pack_start(search, False, False, 0)
    picker_state = {"path": folder, "selected": None, "generation": 0}

    def load_folder(path):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            return
        picker_state["path"] = path
        path_entry.set_text(path)
        picker_state["generation"] += 1
        generation = picker_state["generation"]
        store.clear()
        def scan():
            rows = []
            try:
                query = search.get_text().strip().casefold()
                with os.scandir(path) as entries:
                    for entry in entries:
                        if entry.name.startswith("."):
                            continue
                        if query and query not in entry.name.casefold():
                            continue
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                            if save and not is_dir:
                                continue
                            rows.append((entry.name.casefold(), "folder" if is_dir else "text-x-generic", entry.name, is_dir))
                        except OSError:
                            continue
                rows.sort(key=lambda item: (not item[3], item[0]))
            except OSError:
                rows = []
            def fill():
                if generation != picker_state["generation"]:
                    return False
                query = search.get_text().casefold()
                for _sort, icon_name, name, is_dir in rows:
                    if query in name.casefold():
                        store.append((icon_name, name, os.path.join(path, name), is_dir))
                return False
            GLib.idle_add(fill)
        threading.Thread(target=scan, daemon=True).start()

    def navigate_picker(path):
        load_folder(path)

    def activate_row(_tree, row_path, _column):
        row = store[row_path]
        if row[3]:
            load_folder(row[2])
        elif not save:
            picker_state["selected"] = row[2]
            filename_entry.set_text(row[2])

    tree.connect("row-activated", activate_row)
    path_entry.connect("activate", lambda entry: load_folder(entry.get_text()))
    search.connect("search-changed", lambda _entry: load_folder(picker_state["path"]))
    tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE if save else Gtk.SelectionMode.MULTIPLE)
    tree.connect("cursor-changed", lambda *_: None)
    def selected_paths():
        model, paths = tree.get_selection().get_selected_rows()
        return [model[item][2] for item in paths]
    dialog.set_default_response(1001)
    dialog.show_all()
    load_folder(folder)
    selected = None
    while True:
        response = dialog.run()
        if response != 1001:
            break
        if save:
            name = filename_entry.get_text().strip()
            if not name or name in (".", "..") or "/" in name or "\0" in name:
                show_message("Bitte einen gültigen Dateinamen ohne Pfadtrenner eingeben.", True)
                continue
            selected = os.path.join(picker_state["path"], name)
            break
        candidates = selected_paths() or ([picker_state["selected"]] if picker_state["selected"] else [])
        candidate = candidates[0] if candidates else None
        if candidate and os.path.isdir(candidate):
            load_folder(candidate)
            continue
        if candidates and all(os.path.isfile(path) for path in candidates):
            selected = candidates
            break
        show_message("Bitte eine vorhandene Datei auswählen.")
    dialog.destroy()
    return selected

def load_file(path):
    try:
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            show_message(f"Datei ist {size / (1024 * 1024):.1f} MB groß. Limit: 5 MB.", True)
            return
        with open(path, "r", encoding="utf-8", newline="") as source:
            content = source.read(MAX_FILE_BYTES + 1)
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            show_message("Datei überschreitet das Editorlimit von 5 MB.", True)
            return
    except (OSError, UnicodeError) as error:
        show_message(f"Datei konnte nicht als UTF-8 geöffnet werden:\n{error}", True)
        return
    existing = next((doc for doc in state["documents"] if doc["path"] == path), None)
    if existing:
        notebook.set_current_page(notebook.page_num(existing["page"]))
        return
    add_document(content, path=os.path.abspath(path))

def save_document(doc=None, save_as=False):
    doc = doc or current_document()
    if not doc:
        return False
    path = doc["path"]
    if save_as or not path:
        path = custom_file_dialog(save=True, initial=path or os.path.expanduser("~"))
        if not path:
            return False
    content = doc["buffer"].get_text(doc["buffer"].get_start_iter(), doc["buffer"].get_end_iter(), True)
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        show_message("Datei überschreitet das Editorlimit von 5 MB.", True)
        return False
    directory = os.path.dirname(os.path.abspath(path))
    try:
        fd, temporary = tempfile.mkstemp(prefix=".velos-editor-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            if os.path.exists(path):
                os.chmod(temporary, stat.S_IMODE(os.stat(path).st_mode))
            os.replace(temporary, path)
        except BaseException:
            try: os.unlink(temporary)
            except OSError: pass
            raise
    except OSError as error:
        show_message(f"Datei konnte nicht gespeichert werden:\n{error}", True)
        return False
    doc["path"] = os.path.abspath(path)
    doc["name"] = os.path.basename(path)
    doc["buffer"].set_modified(False)
    set_dirty(doc, False)
    set_language(doc)
    update_tab(doc)
    set_status(f"Gespeichert · {path}")
    return True

def ask_save(doc):
    if not doc["dirty"]:
        return True
    dialog = Gtk.MessageDialog(
        transient_for=window, modal=True, message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.NONE, text="Ungespeicherte Änderungen",
    )
    dialog.format_secondary_text(f"{os.path.basename(doc['path']) if doc['path'] else doc['name']} wurde geändert.")
    dialog.add_button("Verwerfen", Gtk.ResponseType.NO)
    dialog.add_button("Abbrechen", Gtk.ResponseType.CANCEL)
    dialog.add_button("Speichern", Gtk.ResponseType.YES)
    response = dialog.run()
    dialog.destroy()
    if response == Gtk.ResponseType.YES:
        return save_document(doc)
    return response == Gtk.ResponseType.NO

def close_document(doc):
    if not ask_save(doc):
        return
    page_number = notebook.page_num(doc["page"])
    if page_number >= 0:
        notebook.remove_page(page_number)
    state["documents"].remove(doc)
    if not state["documents"]:
        new_document()
    set_status()

def close_editor():
    if state["closing"]:
        return True
    if state["running"]:
        show_message("Warte, bis das laufende Skript beendet wurde.")
        return True
    if "installing" in state["runtime_states"].values():
        show_message("Warte, bis die Interpreter-Installation abgeschlossen ist.")
        return True
    for doc in list(state["documents"]):
        if not ask_save(doc):
            return True
    state["closing"] = True
    Gtk.main_quit()
    return False

def new_document(*_args):
    state["untitled"] += 1
    add_document(name=f"Unbenannt-{state['untitled']}")

def open_documents(*_args):
    selected = custom_file_dialog(save=False)
    if selected:
        paths = selected if isinstance(selected, list) else [selected]
        for path in paths:
            load_file(path)

def find_text(backward=False):
    doc = current_document()
    query = find_entry.get_text()
    if not doc or not query:
        return
    buffer = doc["buffer"]
    selection = buffer.get_selection_bounds()
    start = selection[0] if backward and selection else selection[1] if selection else buffer.get_iter_at_mark(buffer.get_insert())
    result = start.backward_search(query, Gtk.TextSearchFlags.CASE_INSENSITIVE, None) if backward else start.forward_search(query, Gtk.TextSearchFlags.CASE_INSENSITIVE, None)
    if not result:
        start = buffer.get_end_iter() if backward else buffer.get_start_iter()
        result = start.backward_search(query, Gtk.TextSearchFlags.CASE_INSENSITIVE, None) if backward else start.forward_search(query, Gtk.TextSearchFlags.CASE_INSENSITIVE, None)
    if result:
        first, last = result
        buffer.select_range(first, last)
        doc["view"].scroll_to_iter(first, 0.15, False, 0, 0)
    else:
        set_status("Kein weiterer Treffer")

def replace_current(*_args):
    doc = current_document()
    if not doc or not doc["buffer"].get_has_selection():
        find_text()
        return
    buffer = doc["buffer"]
    start, end = buffer.get_selection_bounds()
    buffer.delete(start, end)
    buffer.insert(start, replace_entry.get_text())
    find_text()

def replace_all(*_args):
    doc = current_document()
    needle = find_entry.get_text()
    if not doc or not needle:
        return
    buffer = doc["buffer"]
    cursor = buffer.get_start_iter()
    count = 0
    buffer.begin_user_action()
    try:
        while True:
            result = cursor.forward_search(needle, Gtk.TextSearchFlags.CASE_INSENSITIVE, None)
            if not result:
                break
            start, end = result
            buffer.delete(start, end)
            buffer.insert(start, replace_entry.get_text())
            cursor = start.copy()
            count += 1
            if count >= 10000:
                break
    finally:
        buffer.end_user_action()
    set_status(f"{count} Ersetzungen")

def toggle_search(*_args):
    search_revealer.set_reveal_child(not search_revealer.get_reveal_child())
    if search_revealer.get_reveal_child():
        find_entry.grab_focus()
        find_entry.select_region(0, -1)

def save_selection_as_snippet(*_args):
    doc = current_document()
    if not doc:
        return
    bounds = doc["buffer"].get_selection_bounds()
    if not bounds:
        show_message("Markiere zuerst den Code, den du als Snippet speichern möchtest.")
        return
    dialog = Gtk.Dialog(title="Als Snippet speichern", transient_for=window, modal=True)
    dialog.add_button("Abbrechen", Gtk.ResponseType.CANCEL)
    dialog.add_button("Speichern", Gtk.ResponseType.ACCEPT)
    entry = Gtk.Entry()
    entry.set_placeholder_text("Name des Snippets")
    entry.set_margin_top(16);entry.set_margin_bottom(16);entry.set_margin_start(16);entry.set_margin_end(16)
    dialog.get_content_area().pack_start(entry, True, True, 0)
    dialog.show_all()
    response = dialog.run()
    title = entry.get_text().strip()
    dialog.destroy()
    if response != Gtk.ResponseType.ACCEPT:
        return
    if not title:
        show_message("Der Snippet-Name darf nicht leer sein.", True)
        return
    start, end = bounds
    body = doc["buffer"].get_text(start, end, True)
    enc = lambda value: base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")
    try:
        os.makedirs(os.path.dirname(SNIPPET_STORE), mode=0o700, exist_ok=True)
        fd = os.open(SNIPPET_STORE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="ascii") as output:
            output.write(enc(title) + "\t" + enc(body) + "\n")
    except OSError as error:
        show_message(f"Snippet konnte nicht gespeichert werden:\n{error}", True)
        return
    set_status(f"Snippet gespeichert · {title}")

def insert_snippet(*_args):
    doc = current_document()
    if not doc:
        return
    try:
        with open(SNIPPET_STORE, encoding="ascii") as source:
            rows = source.read().splitlines()
        items = []
        for index, row in enumerate(rows):
            title, body = row.split("\t", 1)
            items.append((index, base64.urlsafe_b64decode(title + "===").decode("utf-8"), base64.urlsafe_b64decode(body + "===").decode("utf-8")))
    except FileNotFoundError:
        show_message("Noch keine Snippets vorhanden. Markiere Code und nutze „Als Snippet speichern“.")
        return
    except (OSError, ValueError, UnicodeError) as error:
        show_message(f"Snippet-Speicher konnte nicht gelesen werden:\n{error}", True)
        return
    dialog = Gtk.Dialog(title="Snippet einfügen", transient_for=window, modal=True)
    dialog.set_default_size(560, 420)
    dialog.add_button("Abbrechen", Gtk.ResponseType.CANCEL)
    dialog.add_button("Einfügen", Gtk.ResponseType.ACCEPT)
    model = Gtk.ListStore(int, str, str)
    for index, title, body in items:
        model.append((index, title, body))
    view = Gtk.TreeView(model=model)
    view.append_column(Gtk.TreeViewColumn("Snippet", Gtk.CellRendererText(), text=1))
    scroller = Gtk.ScrolledWindow()
    scroller.set_min_content_height(300);scroller.add(view)
    dialog.get_content_area().pack_start(scroller, True, True, 8)
    dialog.show_all()
    response = dialog.run()
    selection = view.get_selection()
    selected_model, iterator = selection.get_selected()
    body = selected_model[iterator][2] if iterator else None
    dialog.destroy()
    if response == Gtk.ResponseType.ACCEPT and body is not None:
        doc["buffer"].insert_at_cursor(body)
        doc["view"].grab_focus()

def run_current(*_args):
    doc = current_document()
    if not doc or state["running"]:
        return
    language = doc.get("run_language")
    if not language:
        selected = language_combo.get_active_text()
        if selected == "Automatisch" and doc["path"]:
            language = EXTENSION_LANGUAGES.get(pathlib.Path(doc["path"]).suffix.lower())
        else:
            language = selected
    if language not in RUNNABLE_LANGUAGES:
        if language in ("Windows Batch (nicht ausführbar)", "Malbolge (nicht ausführbar)"):
            show_message(f"{language} kann unter Linux nicht nativ ausgeführt werden.")
        else:
            show_message("Wähle zuerst eine ausführbare Sprache im Sprachmenü aus.")
        return
    runtime_state = state["runtime_states"].get(language)
    if runtime_state == "installing":
        set_status(f"Der Interpreter für {language} wird noch installiert …")
        return
    executable = runtime_executable(language)
    if not executable:
        ensure_runtime_async(language)
        return
    prlimit = shutil.which("prlimit")
    nice = shutil.which("nice")
    if not prlimit or not nice:
        show_message("Für die begrenzte Skriptausführung fehlen 'prlimit' oder 'nice' (Pakete util-linux und coreutils).", True)
        return
    code = doc["buffer"].get_text(doc["buffer"].get_start_iter(), doc["buffer"].get_end_iter(), True)
    if len(code.encode("utf-8")) > MAX_FILE_BYTES:
        show_message("Der aktuelle Code überschreitet das Ausführungslimit von 5 MB.", True)
        return
    state["running"] = True
    run_button.set_sensitive(False)
    output_buffer.set_text(f"Starte {language} …\n")
    output_revealer.set_reveal_child(True)
    output_label.set_text(f"Ausgabe · {language}")
    work_dir = os.path.dirname(os.path.abspath(doc["path"])) if doc["path"] else os.path.expanduser("~")

    def worker():
        chunks = []
        size = 0
        started = time.monotonic()
        timed_out = False
        return_code = 1
        try:
            with tempfile.TemporaryDirectory(prefix="velos-editor-run-") as temp_dir:
                source_suffix = {
                    "Python": ".py", "Bash": ".sh", "Dash": ".sh",
                    "JavaScript": ".js", "Ruby": ".rb", "PHP": ".php",
                    "Perl": ".pl", "C": ".c", "C++": ".cpp",
                    "Rust": ".rs", "Lua": ".lua",
                }[language]
                source_path = os.path.join(temp_dir, "current" + source_suffix)
                with open(source_path, "w", encoding="utf-8", newline="") as source:
                    source.write(code)

                def execute(command):
                    nonlocal size, timed_out
                    remaining = max(0.01, MAX_RUN_SECONDS - (time.monotonic() - started))
                    process = subprocess.Popen(
                        [nice, "-n", "10", prlimit,
                         f"--cpu={MAX_RUN_SECONDS * 2}:{MAX_RUN_SECONDS * 2}",
                         "--as=805306368:805306368", "--fsize=67108864:67108864",
                         "--", *command],
                        cwd=work_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    timeout_fired = threading.Event()
                    watchdog_state = {}
                    def wall_timeout():
                        if process.poll() is None:
                            try:
                                timeout_fired.set()
                                os.killpg(process.pid, signal.SIGTERM)
                                force_watchdog = threading.Timer(
                                    2, lambda: os.killpg(process.pid, signal.SIGKILL)
                                )
                                force_watchdog.daemon = True
                                force_watchdog.start()
                                watchdog_state["force"] = force_watchdog
                            except OSError:
                                return
                    watchdog = threading.Timer(remaining, wall_timeout)
                    watchdog.daemon = True
                    watchdog.start()
                    try:
                        while True:
                            chunk = process.stdout.read(8192)
                            if not chunk:
                                break
                            if size < MAX_RUN_OUTPUT:
                                kept = chunk[:MAX_RUN_OUTPUT - size]
                                chunks.append(kept)
                                size += len(kept)
                        try:
                            result_code = process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            result_code = process.wait()
                    finally:
                        watchdog.cancel()
                        if "force" in watchdog_state:
                            watchdog_state["force"].cancel()
                    timed_out = timed_out or timeout_fired.is_set()
                    return result_code

                if language == "C":
                    return_code = execute([executable, "-std=c11", "-O0", source_path, "-o", os.path.join(temp_dir, "program")])
                    command = [os.path.join(temp_dir, "program")]
                elif language == "C++":
                    return_code = execute([executable, "-std=c++17", "-O0", source_path, "-o", os.path.join(temp_dir, "program")])
                    command = [os.path.join(temp_dir, "program")]
                elif language == "Rust":
                    rust_command = [executable, source_path, "-o", os.path.join(temp_dir, "program")]
                    taskset = shutil.which("taskset")
                    available_cpus = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else set()
                    if taskset and available_cpus:
                        rust_command = [taskset, "-c", str(min(available_cpus)), *rust_command]
                    return_code = execute(rust_command)
                    command = [os.path.join(temp_dir, "program")]
                else:
                    return_code = 0
                    command = [executable, source_path]
                if return_code == 0:
                    return_code = execute(command)

            output = b"".join(chunks).decode("utf-8", "replace")
            if size >= MAX_RUN_OUTPUT:
                output += "\n\n[Ausgabe auf 512 KB begrenzt]"
            if timed_out:
                output += f"\n\n[Zeitlimit von {MAX_RUN_SECONDS} Sekunden erreicht]"
            summary = f"{language} · Status {return_code} · {time.monotonic()-started:.1f} s"
        except Exception as error:
            output = "".join(chunk.decode("utf-8", "replace") for chunk in chunks)
            summary = f"Ausführung von {language} fehlgeschlagen: {error}"
        def finish():
            output_buffer.set_text(output or "(Keine Ausgabe)")
            output_label.set_text(summary)
            state["running"] = False
            run_button.set_sensitive(True)
            set_status(summary)
            return False
        GLib.idle_add(finish)
    threading.Thread(target=worker, daemon=True).start()

def toggle_comment(*_args):
    doc = current_document()
    if not doc:
        return
    language = doc["buffer"].get_language()
    lang_id = language.get_id() if language else ""
    prefix = "#" if lang_id in ("python", "sh", "ruby", "perl", "yaml", "toml") else "//" if lang_id in ("js", "c", "cpp", "rust", "go", "java", "css") else None
    if not prefix:
        set_status("Zeilenkommentar für diese Sprache nicht verfügbar")
        return
    buffer = doc["buffer"]
    if buffer.get_has_selection():
        start, end = buffer.get_selection_bounds()
        first = start.copy();first.set_line_offset(0)
        last = end.copy()
        if last.get_line_offset() == 0 and last.get_line() > first.get_line():
            last.backward_char()
        last.forward_to_line_end()
    else:
        first = buffer.get_iter_at_mark(buffer.get_insert())
        first.set_line_offset(0)
        last = first.copy();last.forward_to_line_end()
    lines = buffer.get_text(first, last, True).splitlines(True)
    nonblank = [line for line in lines if line.strip()]
    uncomment = bool(nonblank) and all(line.lstrip().startswith(prefix) for line in nonblank)
    updated = []
    for line in lines:
        if not line.strip():
            updated.append(line)
        elif uncomment:
            indent = len(line) - len(line.lstrip())
            remainder = line[indent:]
            remainder = remainder[len(prefix):]
            if remainder.startswith(" "):
                remainder = remainder[1:]
            updated.append(line[:indent] + remainder)
        else:
            indent = len(line) - len(line.lstrip())
            updated.append(line[:indent] + prefix + " " + line[indent:])
    buffer.begin_user_action()
    buffer.delete(first, last)
    buffer.insert(first, "".join(updated))
    buffer.end_user_action()

def on_editor_key(_view, event):
    if event.type != Gdk.EventType.KEY_PRESS:
        return False
    ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
    shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
    key = Gdk.keyval_name(event.keyval) or ""
    if ctrl and key.lower() == "s":
        save_document(save_as=shift);return True
    if ctrl and key.lower() == "o":
        open_documents();return True
    if ctrl and key.lower() == "n":
        new_document();return True
    if ctrl and key.lower() == "w":
        doc=current_document()
        if doc: close_document(doc)
        return True
    if ctrl and key.lower() == "f":
        toggle_search();return True
    if ctrl and key.lower() == "h":
        if not search_revealer.get_reveal_child(): search_revealer.set_reveal_child(True)
        replace_entry.grab_focus();return True
    if ctrl and key in ("/", "slash"):
        toggle_comment();return True
    if key == "F5":
        run_current();return True
    if key == "Escape" and search_revealer.get_reveal_child():
        search_revealer.set_reveal_child(False);return True
    return False

def on_language_changed(combo):
    if state["setting_language"]:
        return
    doc = current_document()
    if not doc:
        return
    selected = combo.get_active_text()
    language_id = LANGUAGES.get(selected)
    if selected == "Automatisch":
        set_language(doc)
        doc["run_language"] = EXTENSION_LANGUAGES.get(pathlib.Path(doc["path"]).suffix.lower()) if doc["path"] else None
    else:
        doc["run_language"] = selected
        set_language(doc, language_id or "plain")
    set_status()
    if doc["run_language"] in RUNNABLE_LANGUAGES:
        ensure_runtime_async(doc["run_language"])
    elif doc["run_language"] in ("Windows Batch (nicht ausführbar)", "Malbolge (nicht ausführbar)"):
        set_status(f"{doc['run_language']} ist unter Linux nicht nativ ausführbar.")

def on_tab_changed(*_args):
    set_status()

def choose_open(*_args):
    open_documents()

def on_search_next(*_args):
    find_text()

root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
window.add(root)
toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
toolbar.set_border_width(10)
toolbar.get_style_context().add_class("editor-toolbar")
root.pack_start(toolbar, False, False, 0)
def action_button(label, callback, tooltip=None, primary=False):
    button = Gtk.Button(label=label)
    button.get_style_context().add_class("editor-action")
    if primary:
        button.get_style_context().add_class("suggested-action")
    if tooltip:
        button.set_tooltip_text(tooltip)
    button.connect("clicked", callback)
    toolbar.pack_start(button, False, False, 0)
    return button
action_button("Neu", new_document, "Neue Datei · Ctrl+N")
action_button("Öffnen", choose_open, "Datei öffnen · Ctrl+O")
save_button = action_button("Speichern", lambda *_: save_document(), "Speichern · Ctrl+S")
action_button("Speichern unter", lambda *_: save_document(save_as=True), "Speichern unter · Ctrl+Shift+S")
action_button("Suchen", toggle_search, "Suchen und Ersetzen · Ctrl+F")
action_button("Snippet einfügen", insert_snippet, "Gespeicherten Codebaustein einfügen")
action_button("Als Snippet speichern", save_selection_as_snippet, "Markierung in den Snippet-Hüter übernehmen")
run_button = action_button("▶ Ausführen", run_current, "Aktuelle Editorinhalte mit der ausgewählten Sprache starten · F5", primary=True)
language_combo = Gtk.ComboBoxText()
for name in LANGUAGES:
    language_combo.append_text(name)
language_combo.set_active(0)
language_combo.connect("changed", on_language_changed)
language_combo.set_tooltip_text("Syntaxsprache auswählen")
toolbar.pack_end(language_combo, False, False, 0)

search_revealer = Gtk.Revealer()
search_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
search_bar.set_border_width(8)
search_bar.get_style_context().add_class("search-bar")
search_revealer.add(search_bar)
root.pack_start(search_revealer, False, False, 0)
find_entry = Gtk.SearchEntry()
find_entry.set_placeholder_text("Suchen")
search_bar.pack_start(find_entry, True, True, 0)
replace_entry = Gtk.Entry()
replace_entry.set_placeholder_text("Ersetzen durch")
search_bar.pack_start(replace_entry, True, True, 0)
button = Gtk.Button(label="Zurück")
button.connect("clicked", lambda *_: find_text(backward=True))
search_bar.pack_start(button, False, False, 0)
button = Gtk.Button(label="Weiter")
button.connect("clicked", on_search_next)
search_bar.pack_start(button, False, False, 0)
button = Gtk.Button(label="Ersetzen")
button.connect("clicked", replace_current)
search_bar.pack_start(button, False, False, 0)
button = Gtk.Button(label="Alle ersetzen")
button.connect("clicked", replace_all)
search_bar.pack_start(button, False, False, 0)
find_entry.connect("activate", on_search_next)
replace_entry.connect("activate", replace_current)

notebook = Gtk.Notebook()
notebook.set_scrollable(True)
notebook.connect("switch-page", on_tab_changed)
root.pack_start(notebook, True, True, 0)

output_revealer = Gtk.Revealer()
output_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
output_label = Gtk.Label(label="Ausgabe", xalign=0)
output_label.get_style_context().add_class("output-heading")
output_box.pack_start(output_label, False, False, 0)
output_scroll = Gtk.ScrolledWindow()
output_scroll.set_min_content_height(150)
output_scroll.set_max_content_height(240)
output_buffer = Gtk.TextBuffer()
output_view = Gtk.TextView(buffer=output_buffer)
output_view.set_editable(False);output_view.set_cursor_visible(False);output_view.set_monospace(True)
output_view.get_style_context().add_class("output-view")
output_scroll.add(output_view)
output_box.pack_start(output_scroll, True, True, 0)
output_revealer.add(output_box)
root.pack_start(output_revealer, False, True, 0)

status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
status_box.get_style_context().add_class("editor-status")
status_message = Gtk.Label(label="Bereit", xalign=0)
status_box.pack_start(status_message, True, True, 0)
cursor_label = Gtk.Label(label="Keine Datei geöffnet", xalign=1)
status_box.pack_end(cursor_label, False, False, 0)
root.pack_end(status_box, False, False, 0)

new_document()
window.show_all()
search_revealer.set_reveal_child(False)
output_revealer.set_reveal_child(False)
Gtk.main()
