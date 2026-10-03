import csv
import os
import stat
import sys
import tempfile

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, Pango
except (ImportError, ValueError) as error:
    print(f"Der Tabelleneditor benötigt GTK 3: {error}", file=sys.stderr)
    raise SystemExit(2)

new_file = sys.argv[1] == "--new"
path = "" if new_file else os.path.abspath(sys.argv[1])
dialect = csv.excel
if new_file:
    headers = ["Spalte 1"]
    rows = [[""]]
else:
    try:
        with open(path, encoding="utf-8-sig", newline="") as source:
            sample = source.read(8192)
            source.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            contents = list(csv.reader(source, dialect))
    except (OSError, UnicodeError, csv.Error) as error:
        print(f"CSV-Datei konnte nicht geöffnet werden: {error}", file=sys.stderr)
        raise SystemExit(1)

    if contents:
        headers = contents[0]
        rows = contents[1:]
    else:
        headers = ["Spalte 1"]
        rows = []
column_count = max(1, max([len(headers), *(len(row) for row in rows)], default=1))
headers.extend(f"Spalte {index + 1}" for index in range(len(headers), column_count))
rows = [row + [""] * (column_count - len(row)) for row in rows]
try:
    original_mode = stat.S_IMODE(os.stat(path).st_mode) if path else 0o600
except OSError as error:
    print(f"Dateiberechtigungen konnten nicht gelesen werden: {error}", file=sys.stderr)
    raise SystemExit(1)
dirty = False
if new_file:
    dirty = True

window = Gtk.Window(title=f"Tabellen-Editor · {os.path.basename(path) if path else 'Neue Tabelle'}")
window.set_default_size(1050, 680)
window.set_position(Gtk.WindowPosition.CENTER)
window.set_border_width(16)

layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
window.add(layout)

heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
title = Gtk.Label(label="Tabellen-Editor", xalign=0)
title.get_style_context().add_class("title")
subtitle = Gtk.Label(label=path or "Neue Tabelle · Noch nicht gespeichert", xalign=0)
subtitle.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
subtitle.get_style_context().add_class("dim-label")
heading.pack_start(title, False, False, 0)
heading.pack_start(subtitle, False, False, 0)
layout.pack_start(heading, False, False, 0)

toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
layout.pack_start(toolbar, False, False, 0)

table_area = Gtk.ScrolledWindow()
table_area.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
layout.pack_start(table_area, True, True, 0)

status = Gtk.Label(xalign=0)
layout.pack_start(status, False, False, 0)

table = Gtk.TreeView()
table.set_grid_lines(Gtk.TreeViewGridLines.BOTH)
table.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
table_area.add(table)

def update_status():
    count = len(store)
    status.set_text(f"{count} Zeilen · {len(headers)} Spalten" +
                    (" · Ungespeicherte Änderungen" if dirty else ""))
    window.set_title(("• " if dirty else "") +
                     f"Tabellen-Editor · {os.path.basename(path)}")

def mark_dirty():
    global dirty
    dirty = True
    update_status()

def set_cell(_renderer, tree_path, text, column_index):
    store[tree_path][column_index] = text
    mark_dirty()

def build_table():
    global store
    current_rows = [list(row) for row in store] if "store" in globals() else rows
    store = Gtk.ListStore(*([str] * len(headers)))
    for row in current_rows:
        normalized_row = row[:len(headers)]
        normalized_row.extend([""] * (len(headers) - len(normalized_row)))
        store.append(normalized_row)
    table.set_model(store)
    for column in table.get_columns():
        table.remove_column(column)
    for index, heading_text in enumerate(headers):
        renderer = Gtk.CellRendererText()
        renderer.set_property("editable", True)
        renderer.connect("edited", set_cell, index)
        column = Gtk.TreeViewColumn(heading_text or f"Spalte {index + 1}",
                                    renderer, text=index)
        column.set_resizable(True)
        column.set_min_width(100)
        column.set_sort_column_id(index)
        table.append_column(column)
    update_status()

def show_error(message):
    dialog = Gtk.MessageDialog(
        transient_for=window,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text="Aktion nicht möglich",
    )
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()

def save_to(destination):
    global path, original_mode, dirty
    destination = os.path.abspath(destination)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", dir=os.path.dirname(destination),
            prefix=".vortex-csv-", delete=False
        ) as output:
            temporary = output.name
            writer = csv.writer(output, dialect)
            writer.writerow(headers)
            writer.writerows(store)
            output.flush()
            os.fsync(output.fileno())
        mode = original_mode if destination == path else 0o600
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    except (OSError, UnicodeError, csv.Error) as error:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
        show_error(str(error))
        return False
    path = destination
    original_mode = stat.S_IMODE(os.stat(path).st_mode)
    dirty = False
    subtitle.set_text(path)
    update_status()
    return True

def save():
    return save_to(path) if path else save_as()

def save_as():
    dialog = Gtk.FileChooserDialog(
        title="Tabelle speichern unter",
        parent=window,
        action=Gtk.FileChooserAction.SAVE,
    )
    dialog.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL,
                       "Speichern", Gtk.ResponseType.ACCEPT)
    dialog.set_do_overwrite_confirmation(True)
    dialog.set_current_name(os.path.basename(path) if path else "Tabelle.csv")
    if not path:
        dialog.set_current_folder(os.path.expanduser("~"))
    file_filter = Gtk.FileFilter()
    file_filter.set_name("CSV-Dateien")
    file_filter.add_pattern("*.csv")
    dialog.add_filter(file_filter)
    response = dialog.run()
    destination = dialog.get_filename() if response == Gtk.ResponseType.ACCEPT else None
    dialog.destroy()
    if not destination:
        return False
    return save_to(destination)

def ask_text(title_text, prompt, initial=""):
    dialog = Gtk.Dialog(title=title_text, transient_for=window, modal=True)
    dialog.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL,
                       "OK", Gtk.ResponseType.OK)
    entry = Gtk.Entry()
    entry.set_text(initial)
    entry.set_activates_default(True)
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(12)
    box.pack_start(Gtk.Label(label=prompt, xalign=0), False, False, 0)
    box.pack_start(entry, False, False, 0)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.show_all()
    response = dialog.run()
    value = entry.get_text().strip() if response == Gtk.ResponseType.OK else None
    dialog.destroy()
    return value

def add_row(_button):
    store.append([""] * len(headers))
    mark_dirty()

def remove_row(_button):
    model, iterator = table.get_selection().get_selected()
    if iterator is None:
        show_error("Wähle zuerst eine Zeile aus.")
        return
    model.remove(iterator)
    mark_dirty()

def add_column(_button):
    name = ask_text("Neue Spalte", "Name der Spalte:")
    if name is None:
        return
    if not name:
        name = f"Spalte {len(headers) + 1}"
    headers.append(name)
    build_table()
    mark_dirty()

def remove_column(_button):
    dialog = Gtk.Dialog(title="Spalte entfernen", transient_for=window, modal=True)
    dialog.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL,
                       "Entfernen", Gtk.ResponseType.OK)
    combo = Gtk.ComboBoxText()
    for index, name in enumerate(headers):
        combo.append_text(name or f"Spalte {index + 1}")
    combo.set_active(len(headers) - 1)
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(12)
    box.pack_start(Gtk.Label(label="Zu entfernende Spalte:", xalign=0), False, False, 0)
    box.pack_start(combo, False, False, 0)
    dialog.show_all()
    response = dialog.run()
    index = combo.get_active()
    dialog.destroy()
    if response != Gtk.ResponseType.OK or index < 0:
        return
    if len(headers) == 1:
        show_error("Mindestens eine Spalte muss bestehen bleiben.")
        return
    headers.pop(index)
    for row_index in range(len(store)):
        del store[row_index][index]
    build_table()
    mark_dirty()

def confirm_close():
    if not dirty:
        return True
    dialog = Gtk.MessageDialog(
        transient_for=window,
        modal=True,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.NONE,
        text="Ungespeicherte Änderungen",
    )
    dialog.format_secondary_text("Möchtest du die Änderungen vor dem Schließen speichern?")
    dialog.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL,
                       "Verwerfen", Gtk.ResponseType.NO,
                       "Speichern", Gtk.ResponseType.YES)
    response = dialog.run()
    dialog.destroy()
    if response == Gtk.ResponseType.YES:
        return save()
    return response == Gtk.ResponseType.NO

def close_window(*_args):
    if confirm_close():
        Gtk.main_quit()
    return True

def on_key_press(_widget, event):
    if event.state & Gdk.ModifierType.CONTROL_MASK and event.keyval == Gdk.KEY_s:
        save()
        return True
    return False

for label, callback in (
    ("＋ Zeile", add_row),
    ("− Zeile", remove_row),
    ("＋ Spalte", add_column),
    ("− Spalte", remove_column),
):
    button = Gtk.Button(label=label)
    button.connect("clicked", callback)
    toolbar.pack_start(button, False, False, 0)

spacer = Gtk.Box()
toolbar.pack_start(spacer, True, True, 0)
save_as_button = Gtk.Button(label="Speichern unter…")
save_as_button.connect("clicked", lambda *_: save_as())
toolbar.pack_start(save_as_button, False, False, 0)
save_button = Gtk.Button(label="Speichern")
save_button.get_style_context().add_class("suggested-action")
save_button.connect("clicked", lambda *_: save())
toolbar.pack_start(save_button, False, False, 0)

build_table()
window.connect("delete-event", close_window)
window.connect("key-press-event", on_key_press)
window.show_all()
Gtk.main()
if path:
    print(path)
