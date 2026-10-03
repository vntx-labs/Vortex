#!/usr/bin/env bash

export PATH="$HOME/.local/bin:$PATH"
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
MODULES_DIR="$APP_DIR/modules"

startup_notice() {
    local kind="$1"
    local title="$2"
    local text="$3"

    if command -v yad &>/dev/null; then
        "yad" "--$kind" --center --on-top --title="$title" --text="$text" --width=620
    elif command -v zenity &>/dev/null; then
        "zenity" "--$kind" --title="$title" --text="$text" --width=620
    else
        printf '%s\n%s\n' "$title" "$text" >&2
    fi
}

confirm_dependency_install() {
    local text="$1"

    if command -v yad &>/dev/null; then
        yad --question --center --on-top --title="Fehlende Komponenten installieren?" \
            --text="$text" --width=620
    elif command -v zenity &>/dev/null; then
        zenity --question --title="Fehlende Komponenten installieren?" \
            --text="$text" --width=620
    elif [[ -t 0 ]]; then
        printf '%s\n' "$text" >&2
        read -r -p "Jetzt installieren? [J/n] " answer
        [[ -z "$answer" || "$answer" =~ ^[JjYy]$ ]]
    elif command -v pkexec &>/dev/null; then
        # Bei einem GUI-Start ohne Dialogtool übernimmt die Polkit-Abfrage die Bestätigung.
        return 0
    else
        return 1
    fi
}

SUDO_KEEPALIVE_PID=""
SUDO_ASKPASS_FILE=""

cleanup_admin_session() {
    if [[ -n "$SUDO_KEEPALIVE_PID" ]]; then
        kill "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
        wait "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
        SUDO_KEEPALIVE_PID=""
    fi
    if [[ -n "$SUDO_ASKPASS_FILE" ]]; then
        rm -f -- "$SUDO_ASKPASS_FILE"
    fi
}

initialize_admin_session() {
    local app_pid=$$
    (( EUID == 0 )) && return 0
    command -v sudo &>/dev/null || return 1

    SUDO_ASKPASS_FILE=$(mktemp "${TMPDIR:-/tmp}/velos-askpass.XXXXXX") || return 1
    if ! chmod 700 -- "$SUDO_ASKPASS_FILE"; then
        rm -f -- "$SUDO_ASKPASS_FILE"
        SUDO_ASKPASS_FILE=""
        return 1
    fi
    if ! cat >"$SUDO_ASKPASS_FILE" <<'ASKPASS'
#!/usr/bin/env bash
exec yad --entry --center --on-top --title="VeloOS · Interpreter installieren" \
    --text="Für automatische Interpreter-Installationen wird dein Administratorpasswort benötigt. Es wird nicht gespeichert. VeloOS erneuert die sudo-Freigabe nur, solange die App läuft; danach gilt die Ablaufzeit deiner Systemrichtlinie." \
    --hide-text --width=560
ASKPASS
    then
        rm -f -- "$SUDO_ASKPASS_FILE"
        SUDO_ASKPASS_FILE=""
        return 1
    fi
    if ! chmod 700 -- "$SUDO_ASKPASS_FILE"; then
        rm -f -- "$SUDO_ASKPASS_FILE"
        SUDO_ASKPASS_FILE=""
        return 1
    fi
    if ! SUDO_ASKPASS="$SUDO_ASKPASS_FILE" sudo -A -v; then
        rm -f -- "$SUDO_ASKPASS_FILE"
        SUDO_ASKPASS_FILE=""
        return 1
    fi
    rm -f -- "$SUDO_ASKPASS_FILE"
    SUDO_ASKPASS_FILE=""
    export VELOOS_SUDO_SESSION=1

    (
        local sleep_pid=""
        cleanup_keepalive() {
            if [[ -n "$sleep_pid" ]]; then
                kill "$sleep_pid" 2>/dev/null || true
                wait "$sleep_pid" 2>/dev/null || true
            fi
        }
        trap cleanup_keepalive EXIT TERM INT
        while kill -0 "$app_pid" 2>/dev/null; do
            sleep 60 &
            sleep_pid=$!
            wait "$sleep_pid" || exit 0
            sleep_pid=""
            kill -0 "$app_pid" 2>/dev/null || exit 0
            sudo -n -v 2>/dev/null || exit 0
        done
    ) &
    SUDO_KEEPALIVE_PID=$!
    trap cleanup_admin_session EXIT
}

get_package_manager() {
    local candidate
    for candidate in apt-get dnf pacman zypper apk; do
        if command -v "$candidate" &>/dev/null; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

run_as_admin() {
    if (( EUID == 0 )); then
        "$@"
    elif [[ "${VELOOS_SUDO_SESSION:-0}" == 1 ]] && command -v sudo &>/dev/null &&
        sudo -n -v 2>/dev/null; then
        sudo -n -- "$@"
    elif command -v pkexec &>/dev/null; then
        pkexec "$@"
    elif command -v sudo &>/dev/null && [[ -t 0 ]]; then
        sudo "$@"
    else
        return 126
    fi
}

install_system_packages() {
    local manager="$1"
    shift
    local -a packages=("$@")

    case "$manager" in
        apt-get)
            if [[ "${SKIP_APT_UPDATE:-0}" != 1 ]]; then
                run_as_admin apt-get update || return 1
            fi
            run_as_admin apt-get install -y -- "${packages[@]}"
            ;;
        dnf)
            run_as_admin dnf install -y "${packages[@]}"
            ;;
        pacman)
            run_as_admin pacman -Sy --noconfirm --needed "${packages[@]}"
            ;;
        zypper)
            run_as_admin zypper --non-interactive install "${packages[@]}"
            ;;
        apk)
            run_as_admin apk add --no-progress "${packages[@]}"
            ;;
        *)
            return 1
            ;;
    esac
}




missing_dependencies=()
for cmd in yad python3 find sort basename mktemp readlink cp sleep; do
    command -v "$cmd" &>/dev/null || missing_dependencies+=("$cmd")
done

command -v python3 &>/dev/null || missing_dependencies+=("Python 3")

if [[ ${#missing_dependencies[@]} -gt 0 ]]; then
    package_manager=""
    for candidate in apt-get dnf pacman zypper apk; do
        if command -v "$candidate" &>/dev/null; then
            package_manager="$candidate"
            break
        fi
    done

    if [[ -z "$package_manager" ]]; then
        startup_notice error "Abhängigkeiten fehlen" \
            "Nicht gefunden: ${missing_dependencies[*]}\nKein unterstützter Paketmanager (apt, dnf, pacman, zypper oder apk) wurde erkannt."
        exit 1
    fi

    if (( EUID != 0 )) && ! command -v pkexec &>/dev/null &&
        { ! command -v sudo &>/dev/null || [[ ! -t 0 ]]; }; then
        startup_notice error "Administratorrechte benötigt" \
            "Für die Paketinstallation werden Administratorrechte benötigt. Installiere die fehlenden Pakete über eine Admin-Sitzung oder starte das Skript in einem Terminal mit sudo."
        exit 1
    fi

    case "$package_manager" in
        apt-get) install_packages=(yad python3 python3-gi gir1.2-gtk-3.0 findutils coreutils) ;;
        dnf) install_packages=(yad python3 python3-gobject gtk3 findutils coreutils) ;;
        pacman) install_packages=(yad python python-gobject gtk3 findutils coreutils) ;;
        zypper) install_packages=(yad python3 python3-gobject typelib-1_0-Gtk-3_0 findutils coreutils) ;;
        apk) install_packages=(yad python3 py3-gobject3 gtk+3.0 findutils coreutils) ;;
    esac

    install_description="Benötigt werden: ${missing_dependencies[*]}\nPaketmanager: $package_manager\n\nDas Skript installiert die passenden Systempakete. Dafür kann eine Administrator-Authentifizierung erforderlich sein."
    if ! confirm_dependency_install "$install_description"; then
        startup_notice error "Installation abgebrochen" \
            "Die benötigten Pakete wurden nicht installiert. Das Tool kann ohne sie nicht gestartet werden."
        exit 1
    fi

    startup_notice info "Abhängigkeiten werden installiert" \
        "Die Paketinstallation startet jetzt. Bitte bestätige bei Bedarf die Administrator-Abfrage und warte, bis die Installation abgeschlossen ist."

    install_log="/tmp/dev-and-analyse-install-${UID:-0}-$$.log"
    ( umask 077; set -o noclobber; : > "$install_log" ) 2>/dev/null || {
        startup_notice error "Installationsprotokoll nicht verfügbar" \
            "Ein sicheres Installationsprotokoll konnte nicht angelegt werden."
        exit 1
    }
    if ! install_system_packages "$package_manager" "${install_packages[@]}" >"$install_log" 2>&1; then
        if command -v yad &>/dev/null; then
            yad --text-info --center --on-top --title="Paketinstallation fehlgeschlagen" \
                --filename="$install_log" --width=900 --height=600
        elif command -v zenity &>/dev/null; then
            zenity --text-info --title="Paketinstallation fehlgeschlagen" \
                --filename="$install_log" --width=900 --height=600
        else
            printf 'Paketinstallation fehlgeschlagen. Protokoll: %s\n' "$install_log" >&2
        fi
        exit 1
    fi
    rm -f -- "$install_log"

    missing_after_install=()
    for cmd in yad python3 find sort basename mktemp readlink cp sleep; do
        command -v "$cmd" &>/dev/null || missing_after_install+=("$cmd")
    done
    if [[ ${#missing_after_install[@]} -gt 0 ]]; then
        startup_notice error "Abhängigkeiten weiterhin nicht verfügbar" \
            "Nach der Installation fehlen noch: ${missing_after_install[*]}\nPrüfe die Paketquellen und installierten Systempakete."
        exit 1
    fi
fi

if (( EUID != 0 )) && [[ "${1:-}" != "--module-api" ]]; then
    if ! initialize_admin_session; then
        startup_notice info "Automatische Interpreter-Installation" \
            "Die Admin-Freigabe wurde nicht erteilt. Vorhandene Interpreter funktionieren weiterhin; fehlende können später mit einer erneuten Systemabfrage installiert werden."
    fi
fi

UI=(--center --on-top --modal)

show_error() {
    yad --error "${UI[@]}" --title="Fehler" --text="$1" --width=460
}

# Eigener GTK-Dateibrowser mit Hintergrund-Scanning statt System-Dateiauswahl.
pick_path() {
    local mode="$1"
    local title="$2"
    local current="${3:-$HOME}"
    local selection status

    selection=$(python3 - "$mode" "$title" "$current" <<'PY'
import os
import subprocess
import sys
import threading
import time
import gi

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
except (ImportError, ValueError) as error:
    print(f"Der VeloOS-Dateidialog benötigt GTK 3: {error}", file=sys.stderr)
    raise SystemExit(2)

mode, title, initial = sys.argv[1:]
home = os.path.expanduser("~")

def user_folder(kind, fallback):
    try:
        result = subprocess.run(
            ["xdg-user-dir", kind], capture_output=True, text=True, timeout=1, check=False
        )
        candidate = result.stdout.strip()
        return candidate if candidate and os.path.isdir(candidate) else fallback
    except (OSError, subprocess.TimeoutExpired):
        return fallback

places = [
    ("Persönliche Dateien", home),
    ("Dokumente", user_folder("DOCUMENTS", os.path.join(home, "Documents"))),
    ("Schreibtisch", user_folder("DESKTOP", os.path.join(home, "Desktop"))),
    ("Downloads", user_folder("DOWNLOAD", os.path.join(home, "Downloads"))),
    ("Bilder", user_folder("PICTURES", os.path.join(home, "Pictures"))),
]
places = [(label, path) for label, path in places if os.path.isdir(path)]
if not os.path.isdir(initial):
    initial = home

css = b"""
window.velos-picker { background: #f5f7fb; color: #182338; }
window.velos-picker headerbar { background: #ffffff; border-bottom: 1px solid #dce3ed; }
window.velos-picker .picker-title { color: #14233a; font-size: 20px; font-weight: 700; }
window.velos-picker .picker-subtitle { color: #6b778c; font-size: 12px; }
window.velos-picker .place-button { min-height: 38px; padding: 7px 10px; border: 0; border-radius: 7px; background: transparent; color: #33435c; }
window.velos-picker .place-button:hover { background: #e7eef8; }
window.velos-picker .path-entry { min-height: 36px; padding: 6px 10px; border: 1px solid #d9e1ec; border-radius: 8px; background: #ffffff; }
window.velos-picker treeview { background: #ffffff; color: #25344b; }
window.velos-picker treeview.view:selected { background: #dceaff; color: #123c78; }
window.velos-picker treeview header button { padding: 9px; background: #f6f8fb; color: #65748a; font-weight: 700; }
window.velos-picker .status-label { color: #6b778c; font-size: 11px; }
window.velos-picker button.suggested-action { background: #2563eb; color: #ffffff; font-weight: 700; }
"""
provider = Gtk.CssProvider()
provider.load_from_data(css)
Gtk.StyleContext.add_provider_for_screen(
    Gdk.Screen.get_default(),
    provider,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
)

window = Gtk.Window(title=title)
window.set_name("velos-picker")
window.get_style_context().add_class("velos-picker")
window.set_default_size(1020, 680)
window.set_position(Gtk.WindowPosition.CENTER)
window.set_modal(True)
window.set_keep_above(True)
window.set_border_width(0)

root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
window.add(root)
top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
top.set_border_width(18)
root.pack_start(top, False, False, 0)
heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
heading.pack_start(Gtk.Label(label="Dateien und Ordner", xalign=0), False, False, 0)
heading.get_children()[0].get_style_context().add_class("picker-title")
heading.pack_start(Gtk.Label(label="Nutze Favoriten, Suche oder gib einen Pfad direkt ein.", xalign=0), False, False, 0)
heading.get_children()[1].get_style_context().add_class("picker-subtitle")
top.pack_start(heading, True, True, 0)

nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
root.pack_start(nav, False, False, 0)
nav.set_border_width(0)
for button in (Gtk.Button(label="←"), Gtk.Button(label="↑")):
    button.set_size_request(42, 38)
    nav.pack_start(button, False, False, 0)
back_button, up_button = nav.get_children()
path_entry = Gtk.Entry()
path_entry.set_placeholder_text("Pfad eingeben und Enter drücken")
path_entry.get_style_context().add_class("path-entry")
nav.pack_start(path_entry, True, True, 0)
search_entry = Gtk.SearchEntry()
search_entry.set_placeholder_text("In diesem Ordner suchen")
search_entry.set_size_request(230, -1)
nav.pack_start(search_entry, False, False, 0)
refresh_button = Gtk.Button(label="⟳")
refresh_button.set_tooltip_text("Ordner neu laden")
refresh_button.set_size_request(42, 38)
nav.pack_start(refresh_button, False, False, 0)
nav.set_margin_start(18)
nav.set_margin_end(18)
nav.set_margin_bottom(12)

body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
root.pack_start(body, True, True, 0)
sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
sidebar.set_size_request(200, -1)
sidebar.set_border_width(12)
sidebar.set_margin_start(8)
sidebar_label = Gtk.Label(label="SCHNELLZUGRIFF", xalign=0)
sidebar_label.get_style_context().add_class("picker-subtitle")
sidebar.pack_start(sidebar_label, False, False, 8)
for label, folder in places:
    place_label = Gtk.Label(label=label, xalign=0)
    place_label.set_margin_start(6)
    button = Gtk.Button()
    button.add(place_label)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.get_style_context().add_class("place-button")
    button.connect("clicked", lambda _button, path=folder: navigate(path))
    sidebar.pack_start(button, False, False, 0)
body.pack_start(sidebar, False, False, 0)

separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
body.pack_start(separator, False, False, 0)

store = Gtk.ListStore(str, str, str, str, str, str, bool)
filter_model = store.filter_new()
search_state = {"query": ""}
def visible_row(model, iterator, _data):
    if search_state["query"] not in model.get_value(iterator, 1).casefold():
        return False
    if mode == "directory":
        return model.get_value(iterator, 6)
    return True
filter_model.set_visible_func(visible_row)

tree = Gtk.TreeView(model=filter_model)
tree.set_headers_visible(True)
tree.set_enable_search(False)
tree.get_selection().set_mode(
    Gtk.SelectionMode.MULTIPLE if mode == "text-multi" else Gtk.SelectionMode.SINGLE
)
for label, column_index, width in (
    ("Name", 1, 400), ("Typ", 2, 160), ("Geändert", 3, 170), ("Größe", 4, 95)
):
    column = Gtk.TreeViewColumn(label)
    if column_index == 1:
        icon = Gtk.CellRendererPixbuf()
        column.pack_start(icon, False)
        column.add_attribute(icon, "icon-name", 0)
        renderer = Gtk.CellRendererText()
        column.pack_start(renderer, True)
        column.add_attribute(renderer, "text", column_index)
    else:
        renderer = Gtk.CellRendererText()
        column.pack_start(renderer, True)
        column.add_attribute(renderer, "text", column_index)
    column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
    column.set_fixed_width(width)
    column.set_resizable(True)
    column.set_expand(column_index == 1)
    tree.append_column(column)
scroll = Gtk.ScrolledWindow()
scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
scroll.set_shadow_type(Gtk.ShadowType.IN)
scroll.add(tree)
scroll.set_margin_end(12)
body.pack_start(scroll, True, True, 0)

bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
bottom.set_border_width(14)
bottom.set_margin_start(8)
bottom.set_margin_end(8)
root.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
root.pack_start(bottom, False, False, 0)
status_label = Gtk.Label(label="", xalign=0)
status_label.get_style_context().add_class("status-label")
bottom.pack_start(status_label, True, True, 0)
hidden_toggle = Gtk.CheckButton(label="Versteckte Dateien")
bottom.pack_start(hidden_toggle, False, False, 4)
new_folder_button = Gtk.Button(label="Neuer Ordner")
bottom.pack_start(new_folder_button, False, False, 4)
cancel_button = Gtk.Button(label="Abbrechen")
bottom.pack_start(cancel_button, False, False, 4)
accept_button = Gtk.Button(label="Diesen Ordner verwenden" if mode == "directory" else "Datei(en) öffnen")
accept_button.get_style_context().add_class("suggested-action")
bottom.pack_start(accept_button, False, False, 0)

state = {"current": os.path.abspath(initial), "history": [], "generation": 0, "accepted": None}

def error_dialog(message):
    dialog = Gtk.MessageDialog(
        transient_for=window, modal=True, message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE, text="Dateidialog"
    )
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()

def navigate(path, remember=True):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        error_dialog("Der angegebene Ordner ist nicht verfügbar oder darf nicht geöffnet werden.")
        return
    if remember and path != state["current"]:
        state["history"].append(state["current"])
    state["current"] = path
    path_entry.set_text(path)
    load_directory()

def format_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""

def load_directory():
    state["generation"] += 1
    generation = state["generation"]
    folder = state["current"]
    show_hidden = hidden_toggle.get_active()
    store.clear()
    status_label.set_text("Ordner wird geladen…")
    def scan():
        entries = []
        failure = None
        try:
            with os.scandir(folder) as iterator:
                for item in iterator:
                    if not show_hidden and item.name.startswith("."):
                        continue
                    try:
                        is_dir = item.is_dir(follow_symlinks=False)
                        if mode == "directory" and not is_dir:
                            continue
                        if not is_dir and mode == "csv" and not item.name.casefold().endswith((".csv", ".txt")):
                            continue
                        if not is_dir and mode in ("text", "text-multi") and not item.name.casefold().endswith((".txt", ".md")):
                            continue
                        stat = item.stat(follow_symlinks=False)
                        entries.append((
                            "folder" if is_dir else "text-x-generic",
                            item.name,
                            "Ordner" if is_dir else (item.name.rsplit(".", 1)[-1].upper() if "." in item.name else "Datei"),
                            time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                            "" if is_dir else format_size(stat.st_size),
                            os.path.join(folder, item.name),
                            is_dir,
                        ))
                    except (OSError, PermissionError):
                        continue
            entries.sort(key=lambda row: (not row[6], row[1].casefold()))
        except OSError as error:
            failure = str(error)
        def finish():
            if generation != state["generation"]:
                return False
            if failure:
                status_label.set_text("Ordner konnte nicht gelesen werden")
                error_dialog(f"Ordner konnte nicht gelesen werden:\n{failure}")
                return False
            cursor = 0
            def append_batch():
                nonlocal cursor
                if generation != state["generation"]:
                    return False
                stop = min(cursor + 200, len(entries))
                for entry in entries[cursor:stop]:
                    store.append(entry)
                cursor = stop
                filter_model.refilter()
                if cursor < len(entries):
                    status_label.set_text(f"{cursor} / {len(entries)} Einträge werden geladen…")
                    return True
                visible_count = filter_model.iter_n_children(None)
                status_label.set_text(f"{visible_count} Einträge · {folder}")
                return False
            return append_batch()
        GLib.idle_add(finish)
    threading.Thread(target=scan, daemon=True).start()

def selected_paths():
    model, paths = tree.get_selection().get_selected_rows()
    return [model[path][5] for path in paths]

def accept_selection(*_args):
    if mode == "directory":
        state["accepted"] = [state["current"]]
    else:
        chosen = selected_paths()
        if not chosen:
            status_label.set_text("Bitte zuerst eine Datei auswählen.")
            return
        if any(os.path.isdir(path) for path in chosen):
            if len(chosen) == 1 and os.path.isdir(chosen[0]):
                navigate(chosen[0])
            else:
                status_label.set_text("Bitte Dateien statt Ordner auswählen.")
            return
        state["accepted"] = chosen
    Gtk.main_quit()

def row_activated(_tree, path, _column):
    model = filter_model
    row = model[path]
    if row[6]:
        navigate(row[5])
    elif mode != "directory":
        accept_selection()

def on_search(entry):
    search_state["query"] = entry.get_text().strip().casefold()
    filter_model.refilter()
    status_label.set_text(f"{filter_model.iter_n_children(None)} Treffer · {state['current']}")

def make_folder(*_args):
    dialog = Gtk.Dialog(title="Neuer Ordner", transient_for=window, modal=True)
    dialog.add_button("Abbrechen", Gtk.ResponseType.CANCEL)
    dialog.add_button("Erstellen", Gtk.ResponseType.ACCEPT)
    entry = Gtk.Entry()
    entry.set_placeholder_text("Name des neuen Ordners")
    entry.set_margin_top(14)
    entry.set_margin_bottom(14)
    entry.set_margin_start(14)
    entry.set_margin_end(14)
    dialog.get_content_area().pack_start(entry, True, True, 0)
    dialog.show_all()
    if dialog.run() == Gtk.ResponseType.ACCEPT:
        name = entry.get_text().strip()
        if not name or name in (".", "..") or "/" in name or "\0" in name:
            error_dialog("Bitte einen gültigen Ordnernamen ohne Pfadtrenner eingeben.")
        else:
            try:
                os.mkdir(os.path.join(state["current"], name))
                load_directory()
            except OSError as error:
                error_dialog(f"Ordner konnte nicht erstellt werden:\n{error}")
    dialog.destroy()

back_button.connect("clicked", lambda *_: navigate(state["history"].pop(), remember=False) if state["history"] else None)
up_button.connect("clicked", lambda *_: navigate(os.path.dirname(state["current"])))
refresh_button.connect("clicked", lambda *_: load_directory())
path_entry.connect("activate", lambda entry: navigate(entry.get_text()))
search_entry.connect("search-changed", on_search)
hidden_toggle.connect("toggled", lambda *_: load_directory())
new_folder_button.connect("clicked", make_folder)
cancel_button.connect("clicked", lambda *_: Gtk.main_quit())
accept_button.connect("clicked", accept_selection)
tree.connect("row-activated", row_activated)
window.connect("key-press-event", lambda _window, event: Gtk.main_quit() if event.keyval == Gdk.KEY_Escape else False)
window.connect("destroy", lambda *_: Gtk.main_quit())
window.connect("delete-event", lambda *_: False)
window.show_all()
navigate(initial, remember=False)
Gtk.main()
if state["accepted"]:
    sys.stdout.write("\n".join(state["accepted"]))
    sys.stdout.flush()
    raise SystemExit(0)
raise SystemExit(1)
PY
    )
    status=$?
    case "$status" in
        0) printf '%s\n' "$selection"; return 0 ;;
        1) return 1 ;;
        *) show_error "Der eigene Python-Dateidialog konnte nicht gestartet werden.\n\nPrüfe, ob GTK 3 und die Python-GI-Bindings installiert sind."; return 1 ;;
    esac
}







ensure_optional_tool() {
    local command_name="$1" display_name="$2" package_name="$3" package_manager=""
    local install_log install_pid install_status
    command -v "$command_name" &>/dev/null && return 0
    for candidate in apt-get dnf pacman zypper apk; do
        if command -v "$candidate" &>/dev/null; then
            package_manager="$candidate"
            break
        fi
    done
    [[ -n "$package_manager" ]] || {
        show_error "$display_name ist nicht installiert und es wurde kein unterstützter Paketmanager gefunden."
        return 1
    }
    if ! confirm_dependency_install "$display_name fehlt. Soll das benötigte Paket '$package_name' installiert werden?"; then
        return 1
    fi
    install_log=$(mktemp "${TMPDIR:-/tmp}/velos-deps.XXXXXX") || {
        show_error "Installationsprotokoll für $display_name konnte nicht angelegt werden."
        return 1
    }
    chmod 600 -- "$install_log"
    install_system_packages "$package_manager" "$package_name" >"$install_log" 2>&1 &
    install_pid=$!
    (
        while kill -0 "$install_pid" 2>/dev/null; do
            printf '# Installiere %s. Bitte warten…\n' "$display_name"
            sleep 1
        done
    ) | yad --progress "${UI[@]}" --title="Komponente wird installiert" \
        --text="Installiere $display_name…" --pulsate --auto-close --no-cancel --width=500
    wait "$install_pid"
    install_status=$?
    if ((install_status!=0)); then
        yad --text-info "${UI[@]}" --title="Installation von $display_name fehlgeschlagen" \
            --filename="$install_log" --width=820 --height=520
        rm -f -- "$install_log"
        return 1
    fi
    rm -f -- "$install_log"
    hash -r
    if ! command -v "$command_name" &>/dev/null; then
        show_error "Die Installation wurde beendet, aber '$command_name' ist weiterhin nicht verfügbar."
        return 1
    fi
}

ensure_python_feature() {
    local feature="$1" package_manager="" package_name="" display_name="" install_log install_pid
    local -a packages=()
    case "$feature" in
        markdown)
            display_name="Python-Markdown"
            python3 -c 'import markdown' &>/dev/null && return 0
            ;;
        gtksourceview4)
            display_name="GtkSourceView 4"
            python3 -c 'import gi; gi.require_version("GtkSource", "4"); from gi.repository import GtkSource' &>/dev/null && return 0
            ;;
        webkit2)
            display_name="WebKit2 für GTK 3"
            python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0"); exec("try:\n gi.require_version(\"WebKit2\", \"4.1\")\nexcept ValueError:\n gi.require_version(\"WebKit2\", \"4.0\")"); from gi.repository import WebKit2' &>/dev/null && return 0
            ;;
        *)
            show_error "Unbekannte Python-Komponente: $feature"
            return 2
            ;;
    esac
    for candidate in apt-get dnf pacman zypper apk; do
        if command -v "$candidate" &>/dev/null; then
            package_manager="$candidate"
            break
        fi
    done
    [[ -n "$package_manager" ]] || {
        show_error "$display_name fehlt und es wurde kein unterstützter Paketmanager gefunden."
        return 1
    }
    case "$feature:$package_manager" in
        markdown:apt-get|markdown:dnf) packages=(python3-markdown) ;;
        markdown:pacman) packages=(python-markdown) ;;
        markdown:zypper) packages=(python3-Markdown) ;;
        markdown:apk) packages=(py3-markdown) ;;
        gtksourceview4:apt-get) packages=(gir1.2-gtksource-4) ;;
        gtksourceview4:dnf|gtksourceview4:pacman|gtksourceview4:apk) packages=(gtksourceview4) ;;
        gtksourceview4:zypper) packages=(typelib-1_0-GtkSource-4_0) ;;
        webkit2:apt-get)
            if apt-cache show gir1.2-webkit2-4.1 &>/dev/null; then
                packages=(gir1.2-webkit2-4.1)
            else
                packages=(gir1.2-webkit2-4.0)
            fi
            ;;
        webkit2:dnf) packages=(webkit2gtk4.1) ;;
        webkit2:pacman) packages=(webkit2gtk-4.1) ;;
        webkit2:zypper) packages=(typelib-1_0-WebKit2-4_1) ;;
        webkit2:apk) packages=(webkit2gtk-4.1) ;;
    esac
    if ! confirm_dependency_install "$display_name fehlt. Soll das benötigte Paket '${packages[*]}' installiert werden?"; then
        return 1
    fi
    install_log=$(mktemp "${TMPDIR:-/tmp}/velos-python-feature.XXXXXX") || {
        show_error "Installationsprotokoll für $display_name konnte nicht angelegt werden."
        return 1
    }
    chmod 600 -- "$install_log"
    install_system_packages "$package_manager" "${packages[@]}" >"$install_log" 2>&1 &
    install_pid=$!
    (
        while kill -0 "$install_pid" 2>/dev/null; do
            printf '# Installiere %s. Bitte warten…\n' "$display_name"
            sleep 1
        done
    ) | yad --progress "${UI[@]}" --title="Komponente wird installiert" \
        --text="Installiere $display_name…" --pulsate --auto-close --no-cancel --width=500
    wait "$install_pid"
    if (( $? != 0 )); then
        yad --text-info "${UI[@]}" --title="Installation von $display_name fehlgeschlagen" \
            --filename="$install_log" --width=820 --height=520
        rm -f -- "$install_log"
        return 1
    fi
    rm -f -- "$install_log"
    case "$feature" in
        markdown) python3 -c 'import markdown' &>/dev/null ;;
        gtksourceview4) python3 -c 'import gi; gi.require_version("GtkSource", "4"); from gi.repository import GtkSource' &>/dev/null ;;
        webkit2) python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0"); exec("try:\n gi.require_version(\"WebKit2\", \"4.1\")\nexcept ValueError:\n gi.require_version(\"WebKit2\", \"4.0\")"); from gi.repository import WebKit2' &>/dev/null ;;
    esac || {
        show_error "$display_name wurde installiert, ist aber weiterhin nicht verfügbar."
        return 1
    }
}

copy_to_clipboard() {
    if command -v wl-copy &>/dev/null; then
        wl-copy || { show_error "Die Zwischenablage (Wayland) ist nicht erreichbar."; return 1; }
    elif command -v xclip &>/dev/null; then
        xclip -selection clipboard || { show_error "Die Zwischenablage (X11) ist nicht erreichbar."; return 1; }
    elif command -v xsel &>/dev/null; then
        xsel --clipboard --input || { show_error "Die Zwischenablage (X11) ist nicht erreichbar."; return 1; }
    else
        ensure_optional_tool xclip "Zwischenablage-Unterstützung" xclip || return 1
        xclip -selection clipboard || { show_error "Die Zwischenablage (X11) ist nicht erreichbar."; return 1; }
    fi
}

if [[ "${1:-}" == "--module-api" ]]; then
    case "${2:-}" in
        ensure_optional_tool)
            [[ $# -eq 5 ]] || exit 2
            ensure_optional_tool "$3" "$4" "$5"
            exit $?
            ;;
        ensure_python_feature)
            [[ $# -eq 3 ]] || exit 2
            ensure_python_feature "$3"
            exit $?
            ;;
        pick_path)
            [[ $# -eq 4 ]] || exit 2
            MODULE_SELECTED_PATH=$(pick_path "$3" "$4") || exit 1
            printf '%s\n' "$MODULE_SELECTED_PATH"
            exit 0
            ;;
        show_error)
            [[ $# -eq 3 ]] || exit 2
            show_error "$3"
            exit $?
            ;;
        *)
            printf 'Unbekannte Modul-Core-Aktion: %s\n' "${2:-}" >&2
            exit 2
            ;;
    esac
fi











show_main_menu() {
    python3 - "$MODULES_DIR" <<'PY'
import os
import sys
import gi

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk
except (ImportError, ValueError) as error:
    print(f"Das VeloOS-Menü benötigt GTK 3: {error}", file=sys.stderr)
    raise SystemExit(2)

module_dir = os.path.realpath(sys.argv[1])
sys.path.insert(0, module_dir)
from runtime import ModuleError, load_modules

try:
    modules = [
        (item.module_id, item.name, item.description, item.icon)
        for item in load_modules(module_dir)
    ]
except ModuleError as error:
    print(f"Moduldefinition konnte nicht geladen werden: {error}", file=sys.stderr)
    raise SystemExit(2)

css = b"""
window.velos-home { background: #f3f6fb; color: #14233a; }
window.velos-home headerbar { background: #ffffff; border-bottom: 1px solid #e1e7f0; }
.home-eyebrow { color: #3974d8; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
.home-title { color: #14233a; font-size: 25px; font-weight: 700; }
.home-subtitle { color: #65748a; font-size: 13px; }
.module-card { min-width: 245px; min-height: 116px; padding: 16px; border: 1px solid #e0e7f0; border-radius: 12px; background: #ffffff; color: #18263c; }
.module-card:hover { border-color: #8fb5f5; background: #fafdff; }
.module-name { color: #1b2b43; font-size: 14px; font-weight: 700; }
.module-description { color: #6c7a90; font-size: 11px; }
.module-icon { color: #3478e5; }
.home-footer { color: #7b8798; font-size: 11px; }
"""
provider = Gtk.CssProvider()
provider.load_from_data(css)
Gtk.StyleContext.add_provider_for_screen(
    Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
)

window = Gtk.Window(title="VeloOS · Dev- & CSV-Assistent")
window.get_style_context().add_class("velos-home")
window.set_default_size(980, 700)
window.set_position(Gtk.WindowPosition.CENTER)
window.set_modal(True)
window.set_keep_above(True)
window.set_border_width(26)
layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
window.add(layout)

heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
eyebrow = Gtk.Label(label="VELOOS WORKSPACE", xalign=0)
eyebrow.get_style_context().add_class("home-eyebrow")
heading.pack_start(eyebrow, False, False, 0)
title = Gtk.Label(label="Woran möchtest du arbeiten?", xalign=0)
title.get_style_context().add_class("home-title")
heading.pack_start(title, False, False, 0)
subtitle = Gtk.Label(label="Wähle einen Arbeitsbereich. Deine Werkzeuge sind übersichtlich an einem Ort.", xalign=0)
subtitle.get_style_context().add_class("home-subtitle")
heading.pack_start(subtitle, False, False, 0)
layout.pack_start(heading, False, False, 4)

grid = Gtk.Grid()
grid.set_row_spacing(12)
grid.set_column_spacing(12)
grid.set_column_homogeneous(True)
grid.set_row_homogeneous(True)
layout.pack_start(grid, True, True, 0)
result = {"module": None}

def choose(_button, module):
    result["module"] = module
    Gtk.main_quit()

for index, (module_id, name, description, icon_name) in enumerate(modules):
    card = Gtk.Button()
    card.set_relief(Gtk.ReliefStyle.NONE)
    card.get_style_context().add_class("module-card")
    content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
    icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
    icon.set_pixel_size(34)
    icon.get_style_context().add_class("module-icon")
    content.pack_start(icon, False, False, 0)
    labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
    labels.set_valign(Gtk.Align.CENTER)
    name_label = Gtk.Label(label=name, xalign=0)
    name_label.get_style_context().add_class("module-name")
    labels.pack_start(name_label, False, False, 0)
    description_label = Gtk.Label(label=description, xalign=0)
    description_label.set_line_wrap(True)
    description_label.set_max_width_chars(31)
    description_label.get_style_context().add_class("module-description")
    labels.pack_start(description_label, False, False, 0)
    content.pack_start(labels, True, True, 0)
    card.add(content)
    card.connect("clicked", choose, module_id)
    grid.attach(card, index % 3, index // 3, 1, 1)

footer = Gtk.Label(label="VeloOS · Lokaler Workspace", xalign=0)
footer.get_style_context().add_class("home-footer")
layout.pack_end(footer, False, False, 0)
window.connect("destroy", lambda *_: Gtk.main_quit())
window.show_all()
Gtk.main()
if result["module"]:
    print(result["module"])
    raise SystemExit(0)
raise SystemExit(1)
PY
}

# 2. Hauptmenü
while true; do
    MAIN_CHOICE=$(show_main_menu)
    MENU_STATUS=$?
    [[ "$MENU_STATUS" -eq 1 ]] && exit 0
    if [[ "$MENU_STATUS" -ne 0 ]]; then
        show_error "Das VeloOS-Menü konnte nicht geöffnet werden.\n\nPrüfe, ob GTK 3 und die Python-GI-Bindings installiert sind."
        exit 1
    fi

    MODULE_INFO=$(python3 "$MODULES_DIR/runtime.py" resolve "$MODULES_DIR" "$MAIN_CHOICE") || {
        show_error "Das ausgewählte Modul konnte nicht aus der Modulsammlung geladen werden: $MAIN_CHOICE"
        continue
    }
    IFS=$'\t' read -r MODULE_BACKEND MODULE_ENTRY <<< "$MODULE_INFO"
    if [[ "$MODULE_BACKEND" == "python" ]]; then
        if ! python3 "$MODULES_DIR/$MODULE_ENTRY"; then
            show_error "Das Python-Modul '$MAIN_CHOICE' konnte nicht gestartet werden."
        fi
        continue
    fi
    if [[ "$MODULE_BACKEND" == "dsl" ]]; then
        if ! python3 "$MODULES_DIR/runtime.py" run "$MODULES_DIR" "$MAIN_CHOICE" "$APP_DIR/vortex.sh"; then
            show_error "Das DSL-Modul '$MAIN_CHOICE' konnte nicht ausgeführt werden."
        fi
        continue
    fi
    if [[ "$MODULE_BACKEND" != "shell" ]]; then
        show_error "Das Modul '$MAIN_CHOICE' verwendet ein nicht unterstütztes Backend: $MODULE_BACKEND"
        continue
    fi
    if ! source "$MODULES_DIR/$MODULE_ENTRY"; then
        show_error "Das Modul '$MAIN_CHOICE' konnte nicht geladen werden."
        continue
    fi
    module_main || show_error "Das Modul '$MAIN_CHOICE' wurde mit einem Fehler beendet."
done
