#include "vortex_catalog.h"
#include "vortex_actions.h"
#include "vortex_dialog.h"
#include "vortex_language.h"
#include "vortex_ui.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum {
    MAX_SCRIPT_SIZE = 1024 * 1024,
    WINDOW_WIDTH = 980,
    WINDOW_HEIGHT = 700,
    CARD_WIDTH = 290,
    CARD_HEIGHT = 100,
    CARD_GAP = 14,
    GRID_LEFT = 30,
    GRID_TOP = 150,
    GRID_COLUMNS = 3
};

static char *read_script(const char *module_directory, const char *entry,
                         char *error, size_t error_size)
{
    size_t path_size = strlen(module_directory) + strlen(entry) + 2;
    char *path = malloc(path_size);
    if (path == NULL) {
        snprintf(error, error_size, "Nicht genug Speicher für den Modulpfad.");
        return NULL;
    }
    snprintf(path, path_size, "%s/%s", module_directory, entry);
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        snprintf(error, error_size, "%s: Modulskript konnte nicht geöffnet werden.",
                 path);
        free(path);
        return NULL;
    }
    free(path);

    char *source = malloc(MAX_SCRIPT_SIZE + 1);
    if (source == NULL) {
        fclose(file);
        snprintf(error, error_size, "Nicht genug Speicher für das Modulskript.");
        return NULL;
    }
    size_t length = fread(source, 1, MAX_SCRIPT_SIZE + 1, file);
    int read_error = ferror(file);
    fclose(file);
    if (read_error || length > MAX_SCRIPT_SIZE) {
        snprintf(error, error_size, "Modulskript konnte nicht vollständig gelesen werden "
                 "oder überschreitet 1 MB.");
        free(source);
        return NULL;
    }
    source[length] = '\0';
    return source;
}

static void run_module(VxWindow *window, const char *module_directory,
                       const VxModule *module)
{
    if (strcmp(module->backend, "dsl") != 0) {
        if (!vx_dialog_message(window, module->name,
                               "Dieses Modul wurde noch nicht auf die native C-Runtime "
                               "migriert.")) {
            fprintf(stderr, "%s: natives Modul-Backend wird noch nicht unterstützt.\n",
                    module->module_id);
        }
        return;
    }

    char error[512] = {0};
    char *source = read_script(module_directory, module->entry, error, sizeof(error));
    VxActionContext action_context = {.window = window};
    int status = source == NULL ? 1 : vx_script_run(
        source, vx_actions_handle, &action_context, error, sizeof(error)
    );
    free(source);
    if (status != 0 && !action_context.error_was_shown) {
        if (error[0] == '\0') {
            snprintf(error, sizeof(error), "Modul endete mit Status %d.", status);
        }
        if (!vx_dialog_message(window, module->name, error)) {
            fprintf(stderr, "%s: %s\n", module->module_id, error);
        }
    }
}

static void draw_menu(VxWindow *window, const VxCatalog *catalog)
{
    vx_begin_frame(window, 0xf3f6fbu);
    vx_draw_text(window, 32, 46, 0x3974d8u, "VELOOS WORKSPACE");
    vx_draw_text(window, 32, 83, 0x14233au, "Woran moechtest du arbeiten?");
    vx_draw_text(window, 32, 112, 0x65748au,
                 "Waehle einen Arbeitsbereich. Module werden aus modules/ geladen.");
    for (size_t index = 0; index < catalog->count; ++index) {
        int column = (int)(index % GRID_COLUMNS);
        int row = (int)(index / GRID_COLUMNS);
        int x = GRID_LEFT + column * (CARD_WIDTH + CARD_GAP);
        int y = GRID_TOP + row * (CARD_HEIGHT + CARD_GAP);
        vx_fill_rect(window, x, y, CARD_WIDTH, CARD_HEIGHT, 0xffffffu);
        vx_draw_rect(window, x, y, CARD_WIDTH, CARD_HEIGHT, 0xe0e7f0u);
        vx_draw_text(window, x + 16, y + 34, 0x1b2b43u, catalog->items[index].name);
        vx_draw_text(window, x + 16, y + 61, 0x6c7a90u,
                     catalog->items[index].description);
    }
    vx_draw_text(window, 30, WINDOW_HEIGHT - 24, 0x7b8798u,
                 "VeloOS · Native C / X11");
    vx_present(window);
}

int main(int argc, char **argv)
{
    if (argc == 3 && strcmp(argv[1], "--check") == 0) {
        char error[512] = {0};
        if (!vx_script_check_file(argv[2], error, sizeof(error))) {
            fprintf(stderr, "%s\n", error);
            return 1;
        }
        puts("DSL syntax OK");
        return 0;
    }
    int list_only = argc == 3 && strcmp(argv[1], "--list") == 0;
    const char *module_directory = list_only ? argv[2] : argc == 2 ? argv[1] : NULL;
    if (module_directory == NULL) {
        fprintf(stderr, "Aufruf: vortex-native MODULES_DIR | --list MODULES_DIR | --check SCRIPT\n");
        return 2;
    }
    VxCatalog catalog = {0};
    char error[512] = {0};
    if (!vx_catalog_load(module_directory, &catalog, error, sizeof(error))) {
        fprintf(stderr, "Modulkatalog: %s\n", error);
        return 1;
    }
    if (list_only) {
        for (size_t index = 0; index < catalog.count; ++index) {
            printf("%s\t%s\t%s\t%s\n", catalog.items[index].module_id,
                   catalog.items[index].backend, catalog.items[index].entry,
                   catalog.items[index].name);
        }
        vx_catalog_destroy(&catalog);
        return 0;
    }
    VxWindow *window = vx_window_create(
        "VeloOS · Dev- & CSV-Assistent", WINDOW_WIDTH, WINDOW_HEIGHT
    );
    if (window == NULL) {
        vx_catalog_destroy(&catalog);
        return 1;
    }
    int running = 1;
    int status = 0;
    while (running) {
        VxEvent event;
        if (!vx_window_next_event(window, &event, 1)) {
            continue;
        }
        if (event.type == VX_EVENT_EXPOSE || event.type == VX_EVENT_RESIZE) {
            draw_menu(window, &catalog);
        } else if (event.type == VX_EVENT_POINTER_UP && event.key == 1) {
            for (size_t index = 0; index < catalog.count; ++index) {
                int column = (int)(index % GRID_COLUMNS);
                int row = (int)(index / GRID_COLUMNS);
                int x = GRID_LEFT + column * (CARD_WIDTH + CARD_GAP);
                int y = GRID_TOP + row * (CARD_HEIGHT + CARD_GAP);
                if (event.x >= x && event.x < x + CARD_WIDTH
                    && event.y >= y && event.y < y + CARD_HEIGHT) {
                    run_module(window, module_directory, &catalog.items[index]);
                    draw_menu(window, &catalog);
                    break;
                }
            }
        } else if (event.type == VX_EVENT_CLOSE
                   || (event.type == VX_EVENT_KEY_DOWN
                       && event.key == VX_KEY_ESCAPE)) {
            status = 1;
            running = 0;
        }
    }
    vx_window_destroy(window);
    vx_catalog_destroy(&catalog);
    return status;
}
