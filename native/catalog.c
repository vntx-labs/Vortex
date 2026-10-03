#define _POSIX_C_SOURCE 200809L

#include "vortex_catalog.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int compare_names(const void *left, const void *right)
{
    const char *const *a = left;
    const char *const *b = right;
    return strcmp(*a, *b);
}

static int compare_modules(const void *left, const void *right)
{
    const VxModule *a = left;
    const VxModule *b = right;
    if (a->order != b->order) {
        return a->order < b->order ? -1 : 1;
    }
    return strcmp(a->module_id, b->module_id);
}

static int valid_id(const char *value)
{
    if (value[0] == '\0'
        || (!islower((unsigned char)value[0])
            && !isdigit((unsigned char)value[0]))) {
        return 0;
    }
    for (const unsigned char *cursor = (const unsigned char *)value; *cursor; ++cursor) {
        if (!islower(*cursor) && !isdigit(*cursor) && *cursor != '-') {
            return 0;
        }
    }
    return 1;
}

static char *directive_value(const char *line)
{
    const char *cursor = strchr(line, ' ');
    if (cursor == NULL) {
        return NULL;
    }
    while (*cursor == ' ') {
        ++cursor;
    }
    if (*cursor != '"') {
        if (*cursor == '\0' || strpbrk(cursor, " \t\r\n") != NULL) {
            return NULL;
        }
        return strdup(cursor);
    }
    ++cursor;
    size_t capacity = strlen(cursor) + 1;
    char *result = malloc(capacity);
    if (result == NULL) {
        return NULL;
    }
    size_t length = 0;
    while (*cursor && *cursor != '"') {
        if (*cursor == '\\' && cursor[1] != '\0') {
            ++cursor;
        }
        result[length++] = *cursor++;
    }
    if (*cursor != '"' || cursor[1] != '\0') {
        free(result);
        return NULL;
    }
    result[length] = '\0';
    return result;
}

static int set_directive(VxModule *module, const char *line)
{
    char *value = directive_value(line);
    if (value == NULL) {
        return 0;
    }
    char **destination = NULL;
    if (strncmp(line, "name ", 5) == 0) {
        destination = &module->name;
    } else if (strncmp(line, "description ", 12) == 0) {
        destination = &module->description;
    } else if (strncmp(line, "icon ", 5) == 0) {
        destination = &module->icon;
    } else if (strncmp(line, "backend ", 8) == 0) {
        destination = &module->backend;
    } else if (strncmp(line, "entry ", 6) == 0) {
        destination = &module->entry;
    } else if (strncmp(line, "order ", 6) == 0) {
        char *end = NULL;
        errno = 0;
        long number = strtol(value, &end, 10);
        if (errno || end == value || *end != '\0' || number < INT_MIN || number > INT_MAX) {
            free(value);
            return 0;
        }
        module->order = (int)number;
        free(value);
        return 1;
    } else {
        free(value);
        return -1;
    }
    if (*destination != NULL) {
        free(value);
        return 0;
    }
    *destination = value;
    return 1;
}

static void free_module(VxModule *module)
{
    free(module->module_id);
    free(module->name);
    free(module->description);
    free(module->icon);
    free(module->backend);
    free(module->entry);
    memset(module, 0, sizeof(*module));
}

static int load_one(const char *path, VxModule *module,
                    const char *module_root, char *error, size_t error_size)
{
    FILE *file = fopen(path, "r");
    if (file == NULL) {
        snprintf(error, error_size, "%s: %s", path, strerror(errno));
        return 0;
    }
    char *line = NULL;
    size_t capacity = 0;
    ssize_t length;
    unsigned int seen = 0;
    int line_number = 0;
    int valid = 1;
    while ((length = getline(&line, &capacity, file)) >= 0) {
        ++line_number;
        if ((size_t)length > 16384) {
            snprintf(error, error_size, "%s:%d: Zeile ist zu lang", path, line_number);
            valid = 0;
            break;
        }
        while (length > 0 && (line[length - 1] == '\n' || line[length - 1] == '\r')) {
            line[--length] = '\0';
        }
        char *trimmed = line;
        while (*trimmed == ' ' || *trimmed == '\t') {
            ++trimmed;
        }
        if (*trimmed == '\0' || *trimmed == '#') {
            continue;
        }
        if (module->module_id == NULL) {
            if (strncmp(trimmed, "module ", 7) != 0) {
                valid = 0;
            } else {
                char *id = trimmed + 7;
                if (!valid_id(id)) {
                    valid = 0;
                } else {
                    module->module_id = strdup(id);
                    valid = module->module_id != NULL;
                }
            }
        } else {
            unsigned int bit = 0;
            if (strncmp(trimmed, "name ", 5) == 0) bit = 1u;
            else if (strncmp(trimmed, "description ", 12) == 0) bit = 2u;
            else if (strncmp(trimmed, "icon ", 5) == 0) bit = 4u;
            else if (strncmp(trimmed, "backend ", 8) == 0) bit = 8u;
            else if (strncmp(trimmed, "entry ", 6) == 0) bit = 16u;
            else if (strncmp(trimmed, "order ", 6) == 0) bit = 32u;
            if (bit == 0 || (seen & bit) != 0) {
                valid = 0;
            } else {
                int result = set_directive(module, trimmed);
                if (result <= 0) {
                    valid = 0;
                } else {
                    seen |= bit;
                }
            }
        }
        if (!valid) {
            snprintf(error, error_size, "%s:%d: ungültige Moduldefinition", path, line_number);
            break;
        }
    }
    free(line);
    if (ferror(file)) {
        snprintf(error, error_size, "%s: Lesefehler", path);
        valid = 0;
    }
    fclose(file);
    if (valid && (seen != 63u || module->module_id == NULL
                  || module->name == NULL || module->description == NULL
                  || module->icon == NULL || module->backend == NULL
                  || module->entry == NULL
                  || (strcmp(module->backend, "dsl") != 0
                      && strcmp(module->backend, "python") != 0))) {
        snprintf(error, error_size, "%s: Pflichtfelder fehlen oder Backend ungültig", path);
        valid = 0;
    }
    if (valid) {
        const char *suffix = strcmp(module->backend, "dsl") == 0
            ? ".vscript" : ".py";
        size_t entry_length = strlen(module->entry);
        size_t suffix_length = strlen(suffix);
        if (module->entry[0] == '/' || entry_length <= suffix_length
            || strcmp(module->entry + entry_length - suffix_length, suffix) != 0) {
            snprintf(error, error_size, "%s: ungültiger Moduleinstieg", path);
            valid = 0;
        }
        for (const char *cursor = module->entry; valid && *cursor; ++cursor) {
            if (*cursor == '\\') {
                valid = 0;
            }
        }
        const char *segment = module->entry;
        for (const char *cursor = module->entry; valid; ++cursor) {
            if (*cursor == '/' || *cursor == '\0') {
                size_t segment_length = (size_t)(cursor - segment);
                if (segment_length == 0
                    || (segment_length == 1 && segment[0] == '.')
                    || (segment_length == 2 && segment[0] == '.'
                        && segment[1] == '.')) {
                    valid = 0;
                }
                if (*cursor == '\0') {
                    break;
                }
                segment = cursor + 1;
            }
        }
        if (!valid) {
            snprintf(error, error_size, "%s: ungültiger Moduleinstieg", path);
        }
        char requested[PATH_MAX];
        char resolved[PATH_MAX];
        struct stat info;
        int written = snprintf(requested, sizeof(requested), "%s/%s",
                               module_root, module->entry);
        if (valid && (written < 0 || (size_t)written >= sizeof(requested)
                      || realpath(requested, resolved) == NULL
                      || strncmp(resolved, module_root, strlen(module_root)) != 0
                      || resolved[strlen(module_root)] != '/'
                      || stat(resolved, &info) != 0 || !S_ISREG(info.st_mode))) {
            snprintf(error, error_size, "%s: ungültige oder fehlende Moduldatei",
                     path);
            valid = 0;
        }
    }
    if (!valid) {
        free_module(module);
    }
    return valid;
}

int vx_catalog_load(const char *directory, VxCatalog *catalog,
                    char *error, size_t error_size)
{
    memset(catalog, 0, sizeof(*catalog));
    char resolved_root[PATH_MAX];
    if (realpath(directory, resolved_root) == NULL) {
        snprintf(error, error_size, "%s: %s", directory, strerror(errno));
        return 0;
    }
    DIR *dir = opendir(resolved_root);
    if (dir == NULL) {
        snprintf(error, error_size, "%s: %s", directory, strerror(errno));
        return 0;
    }
    char **names = NULL;
    size_t count = 0;
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        size_t length = strlen(entry->d_name);
        if (length > 5 && strcmp(entry->d_name + length - 5, ".vmod") == 0) {
            char **expanded = realloc(names, (count + 1) * sizeof(*names));
            if (expanded == NULL) {
                snprintf(error, error_size, "Nicht genug Speicher für Moduldateien");
                closedir(dir);
                goto failure;
            }
            names = expanded;
            names[count] = strdup(entry->d_name);
            if (names[count] == NULL) {
                snprintf(error, error_size, "Nicht genug Speicher für Moduldateien");
                closedir(dir);
                goto failure;
            }
            ++count;
        }
    }
    closedir(dir);
    qsort(names, count, sizeof(*names), compare_names);
    for (size_t index = 0; index < count; ++index) {
        char path[PATH_MAX];
        int written = snprintf(path, sizeof(path), "%s/%s", resolved_root, names[index]);
        free(names[index]);
        names[index] = NULL;
        if (written < 0 || (size_t)written >= sizeof(path)) {
            snprintf(error, error_size, "Modulpfad ist zu lang");
            goto failure;
        }
        VxModule module = {0};
        if (!load_one(path, &module, resolved_root, error, error_size)) {
            goto failure;
        }
        for (size_t prior = 0; prior < catalog->count; ++prior) {
            if (strcmp(catalog->items[prior].module_id, module.module_id) == 0) {
                snprintf(error, error_size, "Doppelte Modul-ID: %s", module.module_id);
                free_module(&module);
                goto failure;
            }
        }
        VxModule *expanded = realloc(
            catalog->items, (catalog->count + 1) * sizeof(*catalog->items)
        );
        if (expanded == NULL) {
            snprintf(error, error_size, "Nicht genug Speicher für Modulübersicht");
            free_module(&module);
            goto failure;
        }
        catalog->items = expanded;
        catalog->items[catalog->count++] = module;
    }
    free(names);
    if (catalog->count == 0) {
        snprintf(error, error_size, "Keine .vmod-Module in %s gefunden", directory);
        return 0;
    }
    qsort(catalog->items, catalog->count, sizeof(*catalog->items), compare_modules);
    return 1;

failure:
    for (size_t index = 0; index < count; ++index) {
        free(names[index]);
    }
    free(names);
    vx_catalog_destroy(catalog);
    return 0;
}

void vx_catalog_destroy(VxCatalog *catalog)
{
    for (size_t index = 0; index < catalog->count; ++index) {
        free_module(&catalog->items[index]);
    }
    free(catalog->items);
    memset(catalog, 0, sizeof(*catalog));
}
