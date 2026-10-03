#include "vortex_actions.h"

#include "vortex_dialog.h"
#include "vortex_snippets.h"
#include "vortex_system_actions.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef _WIN32
#include <dirent.h>
#include <limits.h>
#ifndef PATH_MAX
#define PATH_MAX 4096
#endif
#ifndef NAME_MAX
#define NAME_MAX 255
#endif
#endif
#include <sys/stat.h>
#ifndef _WIN32
#include <sys/wait.h>
#include <unistd.h>
#endif

typedef int (*VxActionFunction)(VxWindow *window, size_t argument_count,
                                const char *const *arguments,
                                VxVariables *variables, char *error,
                                size_t error_size);

typedef struct {
    const char *name;
    VxActionFunction function;
} VxActionDefinition;

static int vx_action_arguments(size_t actual, size_t expected,
                               const char *name, char *error,
                               size_t error_size)
{
    if (actual == expected) {
        return 1;
    }
    snprintf(error, error_size, "%s erwartet %zu Argument(e), erhalten: %zu.",
             name, expected, actual);
    return 0;
}

static int vx_action_show_error(VxWindow *window, size_t count,
                               const char *const *arguments,
                               VxVariables *variables, char *error,
                               size_t error_size)
{
    (void)variables;
    if (!vx_action_arguments(count, 1, "core.show_error", error, error_size)) {
        return 2;
    }
    if (!vx_dialog_message(window, "Fehler", arguments[0])) {
        snprintf(error, error_size, "Fehlerdialog konnte nicht angezeigt werden.");
        return 1;
    }
    return 0;
}

static int vx_action_error_message(VxWindow *window, size_t count,
                                   const char *const *arguments,
                                   VxVariables *variables, char *error,
                                   size_t error_size)
{
    (void)variables;
    if (!vx_action_arguments(count, 2, "ui.error", error, error_size)) {
        return 2;
    }
    if (!vx_dialog_message(window, arguments[0], arguments[1])) {
        snprintf(error, error_size, "Dialog '%s' konnte nicht angezeigt werden.",
                 arguments[0]);
        return 1;
    }
    return 0;
}

static int vx_action_info(VxWindow *window, size_t count,
                          const char *const *arguments,
                          VxVariables *variables, char *error,
                          size_t error_size)
{
    (void)variables;
    if (!vx_action_arguments(count, 2, "ui.info", error, error_size)) {
        return 2;
    }
    if (!vx_dialog_message(window, arguments[0], arguments[1])) {
        snprintf(error, error_size, "Dialog '%s' konnte nicht angezeigt werden.",
                 arguments[0]);
        return 1;
    }
    return 0;
}

static int vx_action_confirm(VxWindow *window, size_t count,
                             const char *const *arguments,
                             VxVariables *variables, char *error,
                             size_t error_size)
{
    if (!vx_action_arguments(count, 3, "ui.confirm", error, error_size)) {
        return 2;
    }
    int accepted = 0;
    if (!vx_dialog_confirm(window, arguments[1], arguments[2], &accepted)) {
        snprintf(error, error_size, "Bestätigungsdialog konnte nicht angezeigt werden.");
        return 1;
    }
    if (!vx_variable_set(variables, arguments[0], accepted ? "yes" : "no")) {
        snprintf(error, error_size, "Dialogergebnis konnte nicht gespeichert werden.");
        return 1;
    }
    return 0;
}

static int vx_action_entry(VxWindow *window, size_t count,
                           const char *const *arguments,
                           VxVariables *variables, char *error,
                           size_t error_size)
{
    if (count != 4 && count != 5) {
        snprintf(error, error_size,
                 "ui.entry erwartet RESULT [STATUS] TITLE PROMPT DEFAULT.");
        return 2;
    }
    const char *result_name = arguments[0];
    const char *status_name = count == 5 ? arguments[1] : NULL;
    size_t title_index = count == 5 ? 2 : 1;
    char value[2048];
    int accepted = 0;
    if (!vx_dialog_entry(window, arguments[title_index],
                         arguments[title_index + 1], arguments[title_index + 2],
                         value, sizeof(value), &accepted)) {
        snprintf(error, error_size, "Eingabedialog konnte nicht angezeigt werden.");
        return 1;
    }
    if (!vx_variable_set(variables, result_name, accepted ? value : "")) {
        snprintf(error, error_size, "Eingabe konnte nicht gespeichert werden.");
        return 1;
    }
    if (status_name != NULL
        && !vx_variable_set(variables, status_name, accepted ? "yes" : "no")) {
        snprintf(error, error_size, "Dialogstatus konnte nicht gespeichert werden.");
        return 1;
    }
    return 0;
}

#ifndef _WIN32
static int vx_json_quote(const char *value, char **output, size_t *used,
                         size_t *capacity);

static int vx_append_path_row(char **json, size_t *used, size_t *capacity,
                              const char *path, const char *label)
{
    if (*used + 2 > *capacity) {
        size_t next = *capacity * 2;
        char *expanded = realloc(*json, next);
        if (expanded == NULL) {
            return 0;
        }
        *json = expanded;
        *capacity = next;
    }
    if (*used > 1) {
        (*json)[(*used)++] = ',';
    }
    (*json)[(*used)++] = '[';
    (*json)[*used] = '\0';
    if (!vx_json_quote(path, json, used, capacity)) {
        return 0;
    }
    if (*used + 1 > *capacity) {
        char *expanded = realloc(*json, *capacity * 2);
        if (expanded == NULL) {
            return 0;
        }
        *json = expanded;
        *capacity *= 2;
    }
    (*json)[(*used)++] = ',';
    (*json)[*used] = '\0';
    if (!vx_json_quote(label, json, used, capacity)) {
        return 0;
    }
    if (*used + 2 > *capacity) {
        char *expanded = realloc(*json, *capacity * 2);
        if (expanded == NULL) {
            return 0;
        }
        *json = expanded;
        *capacity *= 2;
    }
    (*json)[(*used)++] = ']';
    (*json)[*used] = '\0';
    return 1;
}

static char *vx_directory_rows(const char *directory, char *error,
                               size_t error_size)
{
    DIR *stream = opendir(directory);
    if (stream == NULL) {
        snprintf(error, error_size, "Ordner konnte nicht gelesen werden: %s",
                 strerror(errno));
        return NULL;
    }
    size_t capacity = 256;
    size_t used = 1;
    size_t count = 0;
    char *json = malloc(capacity);
    if (json == NULL) {
        closedir(stream);
        snprintf(error, error_size, "Nicht genug Speicher für Dateiauswahl.");
        return NULL;
    }
    json[0] = '[';
    json[1] = '\0';
    int valid = 1;
    char parent[PATH_MAX];
    int parent_length = snprintf(parent, sizeof(parent), "%s/..", directory);
    if (parent_length < 0 || (size_t)parent_length >= sizeof(parent)) {
        free(json);
        closedir(stream);
        snprintf(error, error_size, "Übergeordneter Pfad ist zu lang.");
        return NULL;
    }
    char *resolved_parent = realpath(parent, NULL);
    if (resolved_parent != NULL) {
        valid = vx_append_path_row(&json, &used, &capacity,
                                   resolved_parent, "../");
        free(resolved_parent);
    }
    if (!valid) {
        free(json);
        closedir(stream);
        snprintf(error, error_size, "Übergeordneter Ordner konnte nicht aufgenommen werden.");
        return NULL;
    }
    struct dirent *entry;
    while ((entry = readdir(stream)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0) {
            continue;
        }
        if (++count > 2000) {
            snprintf(error, error_size,
                     "Ordner enthält mehr als 2000 Einträge; bitte enger auswählen.");
            valid = 0;
            break;
        }
        size_t path_size = strlen(directory) + strlen(entry->d_name) + 2;
        char *path = malloc(path_size);
        if (path == NULL) {
            snprintf(error, error_size, "Nicht genug Speicher für Dateipfad.");
            valid = 0;
            break;
        }
        snprintf(path, path_size, "%s/%s", directory, entry->d_name);
        struct stat info;
        if (stat(path, &info) != 0
            || (!S_ISDIR(info.st_mode) && !S_ISREG(info.st_mode))) {
            free(path);
            continue;
        }
        char label[NAME_MAX + 16];
        snprintf(label, sizeof(label), "%s%s", entry->d_name,
                 S_ISDIR(info.st_mode) ? "/" : "");
        valid = vx_append_path_row(&json, &used, &capacity, path, label);
        free(path);
        if (!valid) {
            snprintf(error, error_size, "Dateiliste konnte nicht aufgebaut werden.");
            break;
        }
    }
    if (closedir(stream) != 0 && valid) {
        snprintf(error, error_size, "Ordner konnte nicht geschlossen werden: %s",
                 strerror(errno));
        valid = 0;
    }
    if (valid) {
        if (used + 2 > capacity) {
            char *expanded = realloc(json, used + 2);
            if (expanded == NULL) {
                free(json);
                snprintf(error, error_size, "Nicht genug Speicher für Dateiliste.");
                return NULL;
            }
            json = expanded;
        }
        json[used++] = ']';
        json[used] = '\0';
        return json;
    }
    free(json);
    return NULL;
}
#endif

static int vx_action_pick_path(VxWindow *window, size_t count,
                               const char *const *arguments,
                               VxVariables *variables, char *error,
                               size_t error_size)
{
    if (count != 3 || (strcmp(arguments[1], "file") != 0
                       && strcmp(arguments[1], "directory") != 0)) {
        snprintf(error, error_size,
                 "core.pick_path erwartet Ergebnisvariable, file|directory und Titel.");
        return 2;
    }
#ifdef _WIN32
    (void)window;
    (void)variables;
    snprintf(error, error_size, "Native Dateiauswahl ist unter Windows noch nicht portiert.");
    return 2;
#else
    const char *home = getenv("HOME");
    char current[PATH_MAX];
    if (home == NULL || strlen(home) >= sizeof(current)) {
        snprintf(error, error_size, "HOME fehlt oder der Pfad ist zu lang.");
        return 1;
    }
    strcpy(current, home);
    for (;;) {
        char *rows = vx_directory_rows(current, error, error_size);
        if (rows == NULL) {
            return 1;
        }
        char description[PATH_MAX + 128];
        snprintf(description, sizeof(description), "%s\nWähle einen Eintrag. "
                 "Ordner lassen sich öffnen; bei Ordnerauswahl kannst du den "
                 "aktuellen Ordner übernehmen.", current);
        const char *buttons[] = {
            strcmp(arguments[1], "directory") == 0
                ? "Diesen Ordner wählen:0" : "Datei wählen:0",
            "Ordner öffnen:2",
            "Abbrechen:1"
        };
        size_t buttons_capacity = 512;
        size_t buttons_used = 1;
        char *buttons_json = malloc(buttons_capacity);
        if (buttons_json == NULL) {
            free(rows);
            snprintf(error, error_size, "Nicht genug Speicher für Dateiauswahl.");
            return 1;
        }
        buttons_json[0] = '[';
        buttons_json[1] = '\0';
        int serialized = 1;
        for (size_t index = 0; index < 3 && serialized; ++index) {
            if (index > 0) {
                buttons_json[buttons_used++] = ',';
                buttons_json[buttons_used] = '\0';
            }
            serialized = vx_json_quote(buttons[index], &buttons_json,
                                       &buttons_used, &buttons_capacity);
        }
        if (serialized) {
            buttons_json[buttons_used++] = ']';
            buttons_json[buttons_used] = '\0';
        }
        char selected[PATH_MAX] = {0};
        char response[32] = {0};
        int shown = serialized && vx_dialog_list(
            window, arguments[2], description, buttons_json, rows,
            selected, sizeof(selected), response, sizeof(response)
        );
        free(buttons_json);
        free(rows);
        if (!shown) {
            snprintf(error, error_size, "Dateiauswahl konnte nicht angezeigt werden.");
            return 1;
        }
        if (strcmp(response, "1") == 0) {
            return vx_variable_set(variables, arguments[0], "") ? 0 : 1;
        }
        if (strcmp(response, "2") == 0) {
            struct stat info;
            if (selected[0] == '\0' || stat(selected, &info) != 0
                || !S_ISDIR(info.st_mode)) {
                continue;
            }
            char *resolved = realpath(selected, NULL);
            if (resolved == NULL || strlen(resolved) >= sizeof(current)) {
                free(resolved);
                snprintf(error, error_size, "Ordnerpfad konnte nicht aufgelöst werden.");
                return 1;
            }
            strcpy(current, resolved);
            free(resolved);
            continue;
        }
        const char *chosen = selected;
        struct stat info;
        if (strcmp(arguments[1], "directory") == 0) {
            chosen = current;
        } else if (chosen[0] == '\0' || stat(chosen, &info) != 0
                   || !S_ISREG(info.st_mode)) {
            continue;
        }
        char *resolved = realpath(chosen, NULL);
        if (resolved == NULL) {
            snprintf(error, error_size, "Ausgewählter Pfad konnte nicht aufgelöst werden: %s",
                     strerror(errno));
            return 1;
        }
        int stored = vx_variable_set(variables, arguments[0], resolved);
        free(resolved);
        if (!stored) {
            snprintf(error, error_size, "Ausgewählter Pfad konnte nicht gespeichert werden.");
            return 1;
        }
        return 0;
    }
#endif
    return 0;
}

static int vx_action_select_port_process(VxWindow *window, size_t count,
                                         const char *const *arguments,
                                         VxVariables *variables, char *error,
                                         size_t error_size)
{
    if (count != 3) {
        snprintf(error, error_size,
                 "ui.select_port_process erwartet Ergebnisvariable, Port und PID-Liste.");
        return 2;
    }
    char prompt[4096];
    int written = snprintf(prompt, sizeof(prompt),
                           "Verfügbare PIDs für TCP-Port %s:\n%s\n"
                           "PID zum Beenden eingeben (Abbrechen lässt das Feld leer):",
                           arguments[1], arguments[2]);
    if (written < 0 || (size_t)written >= sizeof(prompt)) {
        snprintf(error, error_size, "Prozessliste ist zu lang für die Auswahl.");
        return 1;
    }
    char value[64] = {0};
    int accepted = 0;
    if (!vx_dialog_entry(window, "Prozess auswählen", prompt, "",
                         value, sizeof(value), &accepted)) {
        snprintf(error, error_size, "Prozessauswahl konnte nicht angezeigt werden.");
        return 1;
    }
    if (!accepted || value[0] == '\0') {
        value[0] = '\0';
    } else {
        int found = 0;
        const char *cursor = arguments[2];
        size_t value_length = strlen(value);
        while (*cursor != '\0') {
            const char *end = strchr(cursor, '\n');
            size_t length = end == NULL ? strlen(cursor) : (size_t)(end - cursor);
            if (length == value_length && memcmp(cursor, value, length) == 0) {
                found = 1;
                break;
            }
            cursor = end == NULL ? cursor + length : end + 1;
        }
        if (!found) {
            snprintf(error, error_size,
                     "Ausgewählte PID gehört nicht zu den angezeigten Port-Prozessen.");
            return 1;
        }
    }
    if (!vx_variable_set(variables, arguments[0], value)) {
        snprintf(error, error_size, "Prozessauswahl konnte nicht gespeichert werden.");
        return 1;
    }
    return 0;
}

static int vx_json_quote(const char *value, char **output, size_t *used,
                         size_t *capacity)
{
    size_t length = strlen(value);
    if (length > (SIZE_MAX - *used - 3) / 6) {
        return 0;
    }
    size_t needed = *used + length * 6 + 3;
    if (needed > *capacity) {
        size_t next_capacity = *capacity == 0 ? 64 : *capacity;
        while (next_capacity < needed) {
            if (next_capacity > SIZE_MAX / 2) {
                return 0;
            }
            next_capacity *= 2;
        }
        char *expanded = realloc(*output, next_capacity);
        if (expanded == NULL) {
            return 0;
        }
        *output = expanded;
        *capacity = next_capacity;
    }
    (*output)[(*used)++] = '"';
    static const char hex[] = "0123456789abcdef";
    for (const unsigned char *cursor = (const unsigned char *)value;
         *cursor != '\0'; ++cursor) {
        if (*cursor == '"' || *cursor == '\\') {
            (*output)[(*used)++] = '\\';
            (*output)[(*used)++] = (char)*cursor;
        } else if (*cursor < 0x20) {
            (*output)[(*used)++] = '\\';
            (*output)[(*used)++] = 'u';
            (*output)[(*used)++] = '0';
            (*output)[(*used)++] = '0';
            (*output)[(*used)++] = hex[*cursor >> 4];
            (*output)[(*used)++] = hex[*cursor & 0x0f];
        } else {
            (*output)[(*used)++] = (char)*cursor;
        }
    }
    (*output)[(*used)++] = '"';
    (*output)[*used] = '\0';
    return 1;
}

static char *vx_buttons_json(size_t count, const char *const *buttons)
{
    size_t capacity = 16;
    size_t used = 0;
    char *json = malloc(capacity);
    if (json == NULL) {
        return NULL;
    }
    json[used++] = '[';
    json[used] = '\0';
    for (size_t index = 0; index < count; ++index) {
        if (index > 0) {
            if (used + 2 > capacity) {
                size_t next = capacity * 2;
                char *expanded = realloc(json, next);
                if (expanded == NULL) {
                    free(json);
                    return NULL;
                }
                json = expanded;
                capacity = next;
            }
            json[used++] = ',';
            json[used] = '\0';
        }
        if (!vx_json_quote(buttons[index], &json, &used, &capacity)) {
            free(json);
            return NULL;
        }
    }
    if (used + 2 > capacity) {
        char *expanded = realloc(json, used + 2);
        if (expanded == NULL) {
            free(json);
            return NULL;
        }
        json = expanded;
    }
    json[used++] = ']';
    json[used] = '\0';
    return json;
}

static int vx_action_list(VxWindow *window, size_t count,
                          const char *const *arguments,
                          VxVariables *variables, char *error,
                          size_t error_size)
{
    if (!vx_action_arguments(count, 6, "ui.list", error, error_size)) {
        return 2;
    }
    char selected[4096];
    char response[64];
    if (!vx_dialog_list(window, arguments[2], arguments[3], arguments[4],
                        arguments[5], selected, sizeof(selected),
                        response, sizeof(response))) {
        snprintf(error, error_size,
                 "Listenansicht enthält ungültige Daten oder konnte nicht angezeigt werden.");
        return 1;
    }
    if (!vx_variable_set(variables, arguments[0], selected)
        || !vx_variable_set(variables, arguments[1], response)) {
        snprintf(error, error_size, "Listen-Auswahl konnte nicht gespeichert werden.");
        return 1;
    }
    return 0;
}

static int vx_action_text_info(VxWindow *window, size_t count,
                               const char *const *arguments,
                               VxVariables *variables, char *error,
                               size_t error_size)
{
    if (count < 3) {
        snprintf(error, error_size,
                 "ui.text_info erwartet Ergebnisvariable, Datei, Titel und optionale Schaltflächen.");
        return 2;
    }
    const char *path = vx_variable_get(variables, arguments[1]);
    if (path == NULL) {
        snprintf(error, error_size, "Unbekannte Dateivariable '%s'.", arguments[1]);
        return 2;
    }
    char *buttons = vx_buttons_json(count - 3, &arguments[3]);
    if (buttons == NULL) {
        snprintf(error, error_size, "Schaltflächen konnten nicht serialisiert werden.");
        return 1;
    }
    char response[64] = {0};
    int shown = vx_dialog_text_file(window, arguments[2], path, buttons,
                                    response, sizeof(response));
    free(buttons);
    if (!shown) {
        snprintf(error, error_size, "Textdatei konnte nicht angezeigt werden.");
        return 1;
    }
    if (!vx_variable_set(variables, arguments[0], response)) {
        snprintf(error, error_size, "Dialogantwort konnte nicht gespeichert werden.");
        return 1;
    }
    return 0;
}

static int vx_action_edit_text(VxWindow *window, size_t count,
                              const char *const *arguments,
                              VxVariables *variables, char *error,
                              size_t error_size)
{
    if (!vx_action_arguments(count, 5, "ui.edit_text", error, error_size)) {
        return 2;
    }
    char *value = malloc(65536);
    if (value == NULL) {
        snprintf(error, error_size, "Nicht genug Speicher für den Texteditor.");
        return 1;
    }
    int accepted = 0;
    int shown = vx_dialog_edit_text(window, arguments[2], arguments[3],
                                    arguments[4], value, 65536, &accepted);
    if (!shown) {
        free(value);
        snprintf(error, error_size, "Texteditor konnte nicht geöffnet werden.");
        return 1;
    }
    int stored = vx_variable_set(variables, arguments[0], value)
        && vx_variable_set(variables, arguments[1], accepted ? "yes" : "no");
    free(value);
    if (!stored) {
        snprintf(error, error_size, "Texteditor-Ergebnis konnte nicht gespeichert werden.");
        return 1;
    }
    return 0;
}

#ifndef _WIN32
static int vx_action_clipboard(size_t count, const char *const *arguments,
                               char *error, size_t error_size)
{
    if (!vx_action_arguments(count, 1, "system.clipboard", error, error_size)) {
        return 2;
    }
    size_t length = strlen(arguments[0]);
    if (length > 1024 * 1024) {
        snprintf(error, error_size, "Zwischenablage-Text überschreitet 1 MB.");
        return 1;
    }
    const char *command = getenv("WAYLAND_DISPLAY") != NULL ? "wl-copy"
        : getenv("DISPLAY") != NULL ? "xclip" : NULL;
    if (command == NULL) {
        snprintf(error, error_size,
                 "Keine unterstützte Desktop-Zwischenablage gefunden.");
        return 1;
    }
    int descriptors[2];
    if (pipe(descriptors) != 0) {
        snprintf(error, error_size, "Zwischenablage konnte nicht geöffnet werden: %s",
                 strerror(errno));
        return 1;
    }
    pid_t child = fork();
    if (child < 0) {
        snprintf(error, error_size, "Zwischenablageprogramm konnte nicht gestartet werden: %s",
                 strerror(errno));
        close(descriptors[0]);
        close(descriptors[1]);
        return 1;
    }
    if (child == 0) {
        close(descriptors[1]);
        if (dup2(descriptors[0], STDIN_FILENO) < 0) {
            _exit(126);
        }
        close(descriptors[0]);
        if (strcmp(command, "wl-copy") == 0) {
            execlp(command, command, (char *)NULL);
        } else {
            execlp(command, command, "-selection", "clipboard", (char *)NULL);
        }
        _exit(127);
    }
    close(descriptors[0]);
    size_t written = 0;
    while (written < length) {
        ssize_t amount = write(descriptors[1], arguments[0] + written,
                               length - written);
        if (amount < 0 && errno == EINTR) {
            continue;
        }
        if (amount <= 0) {
            snprintf(error, error_size, "Zwischenablage konnte nicht beschrieben werden: %s",
                     strerror(errno));
            close(descriptors[1]);
            (void)waitpid(child, NULL, 0);
            return 1;
        }
        written += (size_t)amount;
    }
    close(descriptors[1]);
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) {
            snprintf(error, error_size, "Zwischenablageprozess konnte nicht überwacht werden: %s",
                     strerror(errno));
            return 1;
        }
    }
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        snprintf(error, error_size, "%s konnte den Text nicht übernehmen.", command);
        return 1;
    }
    return 0;
}
#endif

static const VxActionDefinition vx_actions[] = {
    {"core.show_error", vx_action_show_error},
    {"core.pick_path", vx_action_pick_path},
    {"ui.error", vx_action_error_message},
    {"ui.info", vx_action_info},
    {"ui.confirm", vx_action_confirm},
    {"ui.entry", vx_action_entry},
    {"ui.select_port_process", vx_action_select_port_process},
    {"ui.list", vx_action_list},
    {"ui.text_info", vx_action_text_info},
    {"ui.edit_text", vx_action_edit_text}
};

int vx_actions_handle(void *context, const char *action, size_t argument_count,
                      const char *const *arguments, VxVariables *variables,
                      char *error, size_t error_size)
{
    int handled = 0;
    int status = vx_snippets_action(action, argument_count, arguments, variables,
                                    &handled, error, error_size);
    if (handled) {
        return status;
    }
#ifndef _WIN32
    if (strcmp(action, "system.clipboard") == 0) {
        return vx_action_clipboard(argument_count, arguments, error, error_size);
    }
#endif
    status = vx_system_action(action, argument_count, arguments, variables,
                              &handled, error, error_size);
    if (handled) {
        return status;
    }
    VxActionContext *action_context = context;
    VxWindow *window = action_context == NULL ? NULL : action_context->window;
    for (size_t index = 0; index < sizeof(vx_actions) / sizeof(vx_actions[0]);
         ++index) {
        if (strcmp(action, vx_actions[index].name) == 0) {
            int status = vx_actions[index].function(
                window, argument_count, arguments, variables, error, error_size
            );
            if (status == 0 && action_context != NULL
                && (strcmp(action, "core.show_error") == 0
                    || strcmp(action, "ui.error") == 0)) {
                action_context->error_was_shown = 1;
            }
            return status;
        }
    }
    snprintf(error, error_size, "Native Aktion '%s' ist noch nicht implementiert.",
             action);
    return 2;
}
