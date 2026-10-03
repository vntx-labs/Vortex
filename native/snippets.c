#define _POSIX_C_SOURCE 200809L

#include "vortex_snippets.h"

#ifdef _WIN32

#include <stdio.h>
#include <string.h>

int vx_snippets_action(const char *action, size_t count,
                       const char *const *arguments, VxVariables *variables,
                       int *handled, char *error, size_t error_size)
{
    (void)count;
    (void)arguments;
    (void)variables;
    *handled = 1;
    snprintf(error, error_size,
             "Native Aktion '%s' ist unter Windows noch nicht portiert.", action);
    return 2;
}

#else

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

enum {
    VX_SNIPPET_FILE_LIMIT = 2 * 1024 * 1024
};

typedef struct {
    char **items;
    size_t count;
} VxSnippetRows;

static const char vx_base64_alphabet[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

static void vx_rows_destroy(VxSnippetRows *rows)
{
    for (size_t index = 0; index < rows->count; ++index) {
        free(rows->items[index]);
    }
    free(rows->items);
    memset(rows, 0, sizeof(*rows));
}

static int vx_snippet_store_path(char *path, size_t path_size,
                                 char *directory, size_t directory_size,
                                 char *error, size_t error_size)
{
    const char *home = getenv("HOME");
    if (home == NULL || home[0] != '/') {
        snprintf(error, error_size, "HOME fehlt oder ist kein absoluter Pfad.");
        return 0;
    }
    int dir_length = snprintf(directory, directory_size,
                              "%s/.local/share/velos", home);
    int path_length = snprintf(path, path_size, "%s/snippets.tsv", directory);
    if (dir_length < 0 || (size_t)dir_length >= directory_size
        || path_length < 0 || (size_t)path_length >= path_size) {
        snprintf(error, error_size, "Der Snippet-Speicherpfad ist zu lang.");
        return 0;
    }
    return 1;
}

static int vx_ensure_directory(const char *path, char *error, size_t error_size)
{
    char copy[PATH_MAX];
    size_t length = strlen(path);
    if (length == 0 || length >= sizeof(copy)) {
        snprintf(error, error_size, "Ungültiger Verzeichnis-Pfad.");
        return 0;
    }
    memcpy(copy, path, length + 1);
    for (char *cursor = copy + 1; ; ++cursor) {
        if (*cursor != '/' && *cursor != '\0') {
            continue;
        }
        char saved = *cursor;
        *cursor = '\0';
        struct stat info;
        if (lstat(copy, &info) != 0) {
            if (errno != ENOENT || mkdir(copy, 0700) != 0) {
                snprintf(error, error_size, "%s konnte nicht erstellt werden: %s",
                         copy, strerror(errno));
                return 0;
            }
            if (lstat(copy, &info) != 0) {
                snprintf(error, error_size, "%s konnte nicht geprüft werden: %s",
                         copy, strerror(errno));
                return 0;
            }
        }
        if (!S_ISDIR(info.st_mode) || S_ISLNK(info.st_mode)) {
            snprintf(error, error_size, "%s ist kein sicheres Verzeichnis.", copy);
            return 0;
        }
        *cursor = saved;
        if (saved == '\0') {
            break;
        }
    }
    return 1;
}

static int vx_validate_store(int create, char *path, size_t path_size,
                             char *directory, size_t directory_size,
                             char *error, size_t error_size)
{
    if (!vx_snippet_store_path(path, path_size, directory, directory_size,
                               error, error_size)) {
        return 0;
    }
    if (create && !vx_ensure_directory(directory, error, error_size)) {
        return 0;
    }
    return 1;
}

static int vx_read_rows(const char *path, VxSnippetRows *rows,
                        char *error, size_t error_size)
{
    int descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (descriptor < 0) {
        snprintf(error, error_size, "%s konnte nicht geöffnet werden: %s",
                 path, strerror(errno));
        return 0;
    }
    struct stat info;
    if (fstat(descriptor, &info) != 0 || !S_ISREG(info.st_mode)) {
        snprintf(error, error_size, "Der Snippet-Speicher ist keine reguläre Datei.");
        close(descriptor);
        return 0;
    }
    if (info.st_size < 0 || (size_t)info.st_size > VX_SNIPPET_FILE_LIMIT) {
        snprintf(error, error_size, "Der Snippet-Speicher überschreitet 2 MB.");
        close(descriptor);
        return 0;
    }
    size_t capacity = (size_t)info.st_size + 1;
    char *contents = malloc(capacity);
    if (contents == NULL) {
        snprintf(error, error_size, "Nicht genug Speicher zum Lesen der Snippets.");
        close(descriptor);
        return 0;
    }
    size_t used = 0;
    while (used < capacity - 1) {
        ssize_t read_count = read(descriptor, contents + used, capacity - 1 - used);
        if (read_count < 0 && errno == EINTR) {
            continue;
        }
        if (read_count < 0) {
            snprintf(error, error_size, "Snippet-Speicher konnte nicht gelesen werden: %s",
                     strerror(errno));
            free(contents);
            close(descriptor);
            return 0;
        }
        if (read_count == 0) {
            break;
        }
        used += (size_t)read_count;
    }
    if (close(descriptor) != 0) {
        snprintf(error, error_size, "Snippet-Speicher konnte nicht geschlossen werden: %s",
                 strerror(errno));
        free(contents);
        return 0;
    }
    contents[used] = '\0';

    char *start = contents;
    for (size_t index = 0; index <= used; ++index) {
        if (contents[index] != '\n' && contents[index] != '\0') {
            continue;
        }
        if (contents + index > start) {
            size_t row_length = (size_t)(contents + index - start);
            char *row = strndup(start, row_length);
            char **expanded = row == NULL ? NULL : realloc(
                rows->items, (rows->count + 1) * sizeof(*expanded)
            );
            if (expanded == NULL) {
                free(row);
                free(contents);
                vx_rows_destroy(rows);
                snprintf(error, error_size, "Nicht genug Speicher für die Snippet-Liste.");
                return 0;
            }
            rows->items = expanded;
            rows->items[rows->count++] = row;
        }
        start = contents + index + 1;
    }
    free(contents);
    return 1;
}

static int vx_decode_base64(const char *encoded, unsigned char **decoded,
                            size_t *decoded_size)
{
    size_t length = strlen(encoded);
    if (length % 4 == 1 || length > VX_SNIPPET_FILE_LIMIT) {
        return 0;
    }
    size_t capacity = length / 4 * 3 + (length % 4 == 0 ? 0 : length % 4 - 1);
    unsigned char *output = malloc(capacity + 1);
    if (output == NULL) {
        return 0;
    }
    size_t out = 0;
    unsigned int accumulator = 0;
    unsigned int bits = 0;
    for (size_t index = 0; index < length; ++index) {
        const char *found = strchr(vx_base64_alphabet, encoded[index]);
        if (found == NULL) {
            free(output);
            return 0;
        }
        accumulator = (accumulator << 6) | (unsigned int)(found - vx_base64_alphabet);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            output[out++] = (unsigned char)((accumulator >> bits) & 0xffu);
        }
    }
    if (bits > 0 && (accumulator & ((1u << bits) - 1u)) != 0) {
        free(output);
        return 0;
    }
    output[out] = '\0';
    *decoded = output;
    *decoded_size = out;
    return 1;
}

static char *vx_encode_base64(const char *value)
{
    const unsigned char *bytes = (const unsigned char *)value;
    size_t length = strlen(value);
    if (length > (SIZE_MAX - 4) / 4 * 3) {
        return NULL;
    }
    size_t capacity = (length / 3) * 4 + (length % 3 == 0 ? 0 : length % 3 + 1);
    char *encoded = malloc(capacity + 1);
    if (encoded == NULL) {
        return NULL;
    }
    size_t out = 0;
    for (size_t index = 0; index < length; index += 3) {
        unsigned int chunk = (unsigned int)bytes[index] << 16;
        if (index + 1 < length) {
            chunk |= (unsigned int)bytes[index + 1] << 8;
        }
        if (index + 2 < length) {
            chunk |= bytes[index + 2];
        }
        encoded[out++] = vx_base64_alphabet[(chunk >> 18) & 63u];
        encoded[out++] = vx_base64_alphabet[(chunk >> 12) & 63u];
        if (index + 1 < length) {
            encoded[out++] = vx_base64_alphabet[(chunk >> 6) & 63u];
        }
        if (index + 2 < length) {
            encoded[out++] = vx_base64_alphabet[chunk & 63u];
        }
    }
    encoded[out] = '\0';
    return encoded;
}

static int vx_decode_row(const char *row, char **title, char **body)
{
    const char *separator = strchr(row, '\t');
    if (separator == NULL || strchr(separator + 1, '\t') != NULL) {
        return 0;
    }
    char *encoded_title = strndup(row, (size_t)(separator - row));
    if (encoded_title == NULL) {
        return 0;
    }
    unsigned char *title_bytes = NULL;
    unsigned char *body_bytes = NULL;
    size_t title_size = 0;
    size_t body_size = 0;
    int valid = vx_decode_base64(encoded_title, &title_bytes, &title_size)
        && vx_decode_base64(separator + 1, &body_bytes, &body_size)
        && memchr(title_bytes, '\0', title_size) == NULL
        && memchr(body_bytes, '\0', body_size) == NULL;
    free(encoded_title);
    if (!valid) {
        free(title_bytes);
        free(body_bytes);
        return 0;
    }
    *title = (char *)title_bytes;
    *body = (char *)body_bytes;
    return 1;
}

static int vx_parse_id(const char *text, size_t *index)
{
    if (text[0] == '\0') {
        return 0;
    }
    size_t value = 0;
    for (const char *cursor = text; *cursor != '\0'; ++cursor) {
        if (*cursor < '0' || *cursor > '9'
            || value > (SIZE_MAX - (size_t)(*cursor - '0')) / 10) {
            return 0;
        }
        value = value * 10 + (size_t)(*cursor - '0');
    }
    if (value == 0) {
        return 0;
    }
    *index = value - 1;
    return 1;
}

static int vx_write_all(int descriptor, const char *data, size_t length)
{
    size_t written = 0;
    while (written < length) {
        ssize_t count = write(descriptor, data + written, length - written);
        if (count < 0 && errno == EINTR) {
            continue;
        }
        if (count <= 0) {
            return 0;
        }
        written += (size_t)count;
    }
    return 1;
}

static int vx_write_rows(const char *path, const char *directory,
                         const VxSnippetRows *rows, char *error,
                         size_t error_size)
{
    size_t template_size = strlen(directory) + sizeof("/.snippets-XXXXXX");
    char *temporary = malloc(template_size);
    if (temporary == NULL) {
        snprintf(error, error_size, "Nicht genug Speicher zum Speichern der Snippets.");
        return 0;
    }
    snprintf(temporary, template_size, "%s/.snippets-XXXXXX", directory);
    int descriptor = mkstemp(temporary);
    if (descriptor < 0) {
        snprintf(error, error_size, "Temporärer Snippet-Speicher konnte nicht erstellt werden: %s",
                 strerror(errno));
        free(temporary);
        return 0;
    }
    int success = fchmod(descriptor, 0600) == 0;
    for (size_t index = 0; success && index < rows->count; ++index) {
        size_t length = strlen(rows->items[index]);
        success = vx_write_all(descriptor, rows->items[index], length)
            && vx_write_all(descriptor, "\n", 1);
    }
    if (success && fsync(descriptor) != 0) {
        success = 0;
    }
    int close_result = close(descriptor);
    if (close_result != 0) {
        success = 0;
    }
    if (success && rename(temporary, path) != 0) {
        success = 0;
    }
    if (!success) {
        int saved_errno = errno;
        (void)unlink(temporary);
        snprintf(error, error_size, "Snippet-Speicher konnte nicht atomar aktualisiert werden: %s",
                 strerror(saved_errno));
    }
    free(temporary);
    return success;
}

static int vx_snippets_initialize(size_t count, char *error, size_t error_size)
{
    if (count != 0) {
        snprintf(error, error_size, "snippets.initialize akzeptiert keine Argumente.");
        return 2;
    }
    char path[PATH_MAX];
    char directory[PATH_MAX];
    if (!vx_validate_store(1, path, sizeof(path), directory, sizeof(directory),
                           error, error_size)) {
        return 1;
    }
    int descriptor = open(path, O_CREAT | O_APPEND | O_WRONLY | O_CLOEXEC
                         | O_NOFOLLOW | O_NONBLOCK, 0600);
    if (descriptor < 0) {
        snprintf(error, error_size, "Snippet-Speicher konnte nicht vorbereitet werden: %s",
                 strerror(errno));
        return 1;
    }
    struct stat info;
    int valid = fstat(descriptor, &info) == 0 && S_ISREG(info.st_mode);
    if (valid && fchmod(descriptor, 0600) != 0) {
        valid = 0;
    }
    int saved_errno = errno;
    close(descriptor);
    if (!valid) {
        snprintf(error, error_size, "Snippet-Speicher ist ungültig oder konnte nicht geschützt werden: %s",
                 strerror(saved_errno));
        return 1;
    }
    return 0;
}

static int vx_snippets_list(size_t count, const char *const *arguments,
                            VxVariables *variables, char *error,
                            size_t error_size)
{
    if (count != 1) {
        snprintf(error, error_size, "snippets.list erwartet eine Ergebnisvariable.");
        return 2;
    }
    char path[PATH_MAX];
    char directory[PATH_MAX];
    VxSnippetRows rows = {0};
    if (!vx_validate_store(0, path, sizeof(path), directory, sizeof(directory),
                           error, error_size)
        || !vx_read_rows(path, &rows, error, error_size)) {
        vx_rows_destroy(&rows);
        return 1;
    }
    size_t capacity = 2;
    char *json = calloc(1, capacity);
    if (json == NULL) {
        vx_rows_destroy(&rows);
        snprintf(error, error_size, "Nicht genug Speicher für die Snippet-Liste.");
        return 1;
    }
    size_t used = 1;
    json[0] = '[';
    int success = 1;
    for (size_t index = 0; success && index < rows.count; ++index) {
        char *title = NULL;
        char *body = NULL;
        if (!vx_decode_row(rows.items[index], &title, &body)) {
            continue;
        }
        free(body);
        char id[32];
        int id_length = snprintf(id, sizeof(id), "%zu", index + 1);
        size_t needed = strlen(title) * 6 + (size_t)id_length + 8;
        if (needed > SIZE_MAX - used - 1) {
            success = 0;
            free(title);
            break;
        }
        if (used + needed + 1 > capacity) {
            capacity = used + needed + 1;
            char *expanded = realloc(json, capacity);
            if (expanded == NULL) {
                success = 0;
                free(title);
                break;
            }
            json = expanded;
        }
        if (used > 1) {
            json[used++] = ',';
        }
        json[used++] = '[';
        memcpy(json + used, id, (size_t)id_length);
        used += (size_t)id_length;
        json[used++] = ',';
        json[used++] = '"';
        for (const unsigned char *cursor = (const unsigned char *)title;
             *cursor != '\0'; ++cursor) {
            if (*cursor == '"' || *cursor == '\\') {
                json[used++] = '\\';
                json[used++] = (char)*cursor;
            } else if (*cursor < 0x20) {
                static const char hex[] = "0123456789abcdef";
                json[used++] = '\\';
                json[used++] = 'u';
                json[used++] = '0';
                json[used++] = '0';
                json[used++] = hex[*cursor >> 4];
                json[used++] = hex[*cursor & 0x0f];
            } else {
                json[used++] = (char)*cursor;
            }
        }
        json[used++] = '"';
        json[used++] = ']';
        json[used] = '\0';
        free(title);
    }
    vx_rows_destroy(&rows);
    if (success) {
        char *expanded = realloc(json, used + 2);
        if (expanded == NULL) {
            success = 0;
        } else {
            json = expanded;
            json[used++] = ']';
            json[used] = '\0';
        }
    }
    if (!success || !vx_variable_set(variables, arguments[0], json)) {
        free(json);
        snprintf(error, error_size, "Snippet-Liste konnte nicht gespeichert werden.");
        return 1;
    }
    free(json);
    return 0;
}

static int vx_snippets_get(size_t count, const char *const *arguments,
                           VxVariables *variables, char *error,
                           size_t error_size)
{
    if (count != 3) {
        snprintf(error, error_size,
                 "snippets.get erwartet Ergebnisvariablen für Name und Inhalt sowie eine ID.");
        return 2;
    }
    size_t index;
    if (!vx_parse_id(arguments[2], &index)) {
        snprintf(error, error_size, "Ungültige Snippet-ID.");
        return 1;
    }
    char path[PATH_MAX];
    char directory[PATH_MAX];
    VxSnippetRows rows = {0};
    if (!vx_validate_store(0, path, sizeof(path), directory, sizeof(directory),
                           error, error_size)
        || !vx_read_rows(path, &rows, error, error_size)) {
        vx_rows_destroy(&rows);
        return 1;
    }
    if (index >= rows.count) {
        vx_rows_destroy(&rows);
        snprintf(error, error_size, "Die Snippet-ID ist nicht mehr vorhanden.");
        return 1;
    }
    char *title = NULL;
    char *body = NULL;
    int valid = vx_decode_row(rows.items[index], &title, &body);
    vx_rows_destroy(&rows);
    if (!valid) {
        snprintf(error, error_size, "Der gespeicherte Snippet ist beschädigt.");
        return 1;
    }
    if (!vx_variable_set(variables, arguments[0], title)
        || !vx_variable_set(variables, arguments[1], body)) {
        free(title);
        free(body);
        snprintf(error, error_size, "Snippet konnte nicht in DSL-Variablen geladen werden.");
        return 1;
    }
    free(title);
    free(body);
    return 0;
}

static int vx_snippets_mutate(int deleting, size_t count,
                              const char *const *arguments,
                              VxVariables *variables, char *error,
                              size_t error_size)
{
    size_t expected = deleting ? 1 : 3;
    const char *action = deleting ? "snippets.delete" : "snippets.save";
    if (count != expected) {
        snprintf(error, error_size, "%s erwartet %zu Argument(e).", action, expected);
        return 2;
    }
    if (!deleting && arguments[1][0] == '\0') {
        snprintf(error, error_size, "Der Snippet-Name darf nicht leer sein.");
        return 1;
    }
    char path[PATH_MAX];
    char directory[PATH_MAX];
    VxSnippetRows rows = {0};
    if (!vx_validate_store(1, path, sizeof(path), directory, sizeof(directory),
                           error, error_size)
        || !vx_read_rows(path, &rows, error, error_size)) {
        vx_rows_destroy(&rows);
        return 1;
    }
    size_t index = rows.count;
    int append = !deleting && strcmp(arguments[0], "new") == 0;
    if (!append) {
        if (!vx_parse_id(arguments[0], &index)) {
            vx_rows_destroy(&rows);
            snprintf(error, error_size, "Ungültige Snippet-ID.");
            return 1;
        }
        if (index >= rows.count) {
            vx_rows_destroy(&rows);
            snprintf(error, error_size, deleting
                     ? "Die Snippet-ID ist nicht mehr vorhanden."
                     : "Der Eintrag wurde zwischenzeitlich geändert.");
            return 1;
        }
    }

    char *encoded_row = NULL;
    if (!deleting) {
        char *title = vx_encode_base64(arguments[1]);
        char *body = vx_encode_base64(arguments[2]);
        if (title == NULL || body == NULL) {
            free(title);
            free(body);
            vx_rows_destroy(&rows);
            snprintf(error, error_size, "Snippet konnte nicht codiert werden.");
            return 1;
        }
        size_t row_size = strlen(title) + strlen(body) + 2;
        encoded_row = malloc(row_size);
        if (encoded_row != NULL) {
            snprintf(encoded_row, row_size, "%s\t%s", title, body);
        }
        free(title);
        free(body);
        if (encoded_row == NULL) {
            vx_rows_destroy(&rows);
            snprintf(error, error_size, "Nicht genug Speicher zum Speichern des Snippets.");
            return 1;
        }
    }

    if (deleting) {
        free(rows.items[index]);
        memmove(rows.items + index, rows.items + index + 1,
                (rows.count - index - 1) * sizeof(*rows.items));
        --rows.count;
    } else if (append) {
        char **expanded = realloc(rows.items, (rows.count + 1) * sizeof(*expanded));
        if (expanded == NULL) {
            free(encoded_row);
            vx_rows_destroy(&rows);
            snprintf(error, error_size, "Nicht genug Speicher für den Snippet.");
            return 1;
        }
        rows.items = expanded;
        rows.items[rows.count++] = encoded_row;
    } else {
        free(rows.items[index]);
        rows.items[index] = encoded_row;
    }
    int success = vx_write_rows(path, directory, &rows, error, error_size);
    vx_rows_destroy(&rows);
    (void)variables;
    return success ? 0 : 1;
}

int vx_snippets_action(const char *action, size_t count,
                       const char *const *arguments, VxVariables *variables,
                       int *handled, char *error, size_t error_size)
{
    *handled = 1;
    if (strcmp(action, "snippets.initialize") == 0) {
        return vx_snippets_initialize(count, error, error_size);
    }
    if (strcmp(action, "snippets.list") == 0) {
        return vx_snippets_list(count, arguments, variables, error, error_size);
    }
    if (strcmp(action, "snippets.get") == 0) {
        return vx_snippets_get(count, arguments, variables, error, error_size);
    }
    if (strcmp(action, "snippets.save") == 0) {
        return vx_snippets_mutate(0, count, arguments, variables, error, error_size);
    }
    if (strcmp(action, "snippets.delete") == 0) {
        return vx_snippets_mutate(1, count, arguments, variables, error, error_size);
    }
    *handled = 0;
    return 0;
}

#endif
