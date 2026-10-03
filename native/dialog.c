#define _POSIX_C_SOURCE 200809L

#include "vortex_dialog.h"

#ifndef _WIN32
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum {
    VX_TEXT_LIMIT = 112,
    VX_BUTTON_Y = 430,
    VX_LIST_X = 72,
    VX_LIST_Y = 72,
    VX_LIST_WIDTH = 836,
    VX_LIST_HEIGHT = 550,
    VX_ROW_HEIGHT = 36
};

typedef struct {
    char **items;
    size_t count;
} VxStringList;

typedef struct {
    char *first;
    char *second;
} VxDialogRow;

typedef struct {
    VxDialogRow *items;
    size_t count;
} VxDialogRows;

typedef enum {
    VX_MESSAGE,
    VX_CONFIRM,
    VX_ENTRY,
    VX_EDIT
} VxDialogKind;

static void vx_draw_button(VxWindow *window, int x, int y, const char *label)
{
    vx_fill_rect(window, x, y, 116, 38, 0xe8eef6u);
    vx_draw_rect(window, x, y, 116, 38, 0xb8c5d6u);
    vx_draw_text(window, x + 12, y + 25, 0x24364fu, label);
}

static void vx_draw_lines(VxWindow *window, int x, int y, int line_height,
                          int max_lines, const char *text, uint32_t color)
{
    const char *cursor = text;
    for (int line_no = 0; *cursor != '\0' && line_no < max_lines; ++line_no) {
        char line[VX_TEXT_LIMIT + 1];
        size_t length = 0;
        while (cursor[length] != '\0' && cursor[length] != '\n'
               && !(cursor[length] == '\\' && cursor[length + 1] == 'n')
               && length < VX_TEXT_LIMIT) {
            ++length;
        }
        memcpy(line, cursor, length);
        line[length] = '\0';
        vx_draw_text(window, x, y + line_no * line_height, color, line);
        cursor += length;
        if (*cursor == '\n') {
            ++cursor;
        } else if (cursor[0] == '\\' && cursor[1] == 'n') {
            cursor += 2;
        }
    }
}

static void vx_draw_modal(VxWindow *window, VxDialogKind kind,
                          const char *title, const char *text,
                          const char *value)
{
    vx_begin_frame(window, 0xf3f6fbu);
    vx_fill_rect(window, 140, 150, 700, 410, 0xffffffu);
    vx_draw_rect(window, 140, 150, 700, 410, 0xcbd5e2u);
    vx_draw_text(window, 164, 190, 0x1b2b43u, title);
    vx_draw_lines(window, 164, 226, 24, 5, text, 0x52637au);
    if (kind == VX_ENTRY) {
        vx_fill_rect(window, 164, 330, 652, 38, 0xf7f9fcu);
        vx_draw_rect(window, 164, 330, 652, 38, 0xb8c5d6u);
        vx_draw_text(window, 174, 355, 0x24364fu, value);
    } else if (kind == VX_EDIT) {
        vx_fill_rect(window, 164, 310, 652, 180, 0xf7f9fcu);
        vx_draw_rect(window, 164, 310, 652, 180, 0xb8c5d6u);
        vx_draw_lines(window, 174, 334, 20, 8, value, 0x24364fu);
    }
    if (kind != VX_MESSAGE) {
        vx_draw_button(window, 560, VX_BUTTON_Y, "Abbrechen");
    }
    vx_draw_button(window, 692, VX_BUTTON_Y,
                   kind == VX_CONFIRM || kind == VX_EDIT ? "Speichern" : "OK");
    vx_present(window);
}

static int vx_modal_run(VxWindow *window, VxDialogKind kind,
                        const char *title, const char *text, char *value,
                        size_t value_size, int *accepted)
{
    if (window == NULL || title == NULL || text == NULL || accepted == NULL
        || ((kind == VX_ENTRY || kind == VX_EDIT)
            && (value == NULL || value_size == 0))) {
        return 0;
    }
    *accepted = 0;
    vx_draw_modal(window, kind, title, text, value == NULL ? "" : value);
    for (;;) {
        VxEvent event;
        if (!vx_window_next_event(window, &event, 1)) {
            continue;
        }
        if (event.type == VX_EVENT_EXPOSE || event.type == VX_EVENT_RESIZE) {
            vx_draw_modal(window, kind, title, text, value == NULL ? "" : value);
            continue;
        }
        if (event.type == VX_EVENT_CLOSE
            || (event.type == VX_EVENT_KEY_DOWN && event.key == VX_KEY_ESCAPE)) {
            return 1;
        }
        if (event.type == VX_EVENT_KEY_DOWN && event.key == VX_KEY_RETURN) {
            if (kind == VX_EDIT) {
                size_t length = strlen(value);
                if (length + 1 < value_size) {
                    value[length] = '\n';
                    value[length + 1] = '\0';
                    vx_draw_modal(window, kind, title, text, value);
                }
            } else {
                *accepted = 1;
                return 1;
            }
            continue;
        }
        if (event.type == VX_EVENT_KEY_DOWN && event.key == VX_KEY_BACKSPACE
            && (kind == VX_ENTRY || kind == VX_EDIT)) {
            size_t length = strlen(value);
            if (length > 0) {
                do {
                    --length;
                } while (length > 0
                         && ((unsigned char)value[length] & 0xc0u) == 0x80u);
                value[length] = '\0';
                vx_draw_modal(window, kind, title, text, value);
            }
            continue;
        }
        if (event.type == VX_EVENT_TEXT
            && (kind == VX_ENTRY || kind == VX_EDIT)) {
            size_t length = strlen(value);
            size_t added = strlen(event.text);
            if (added < value_size - length
                && memchr(event.text, '\n', added) == NULL) {
                memcpy(value + length, event.text, added + 1);
                vx_draw_modal(window, kind, title, text, value);
            }
            continue;
        }
        if (event.type == VX_EVENT_POINTER_UP && event.key == 1
            && event.y >= VX_BUTTON_Y && event.y < VX_BUTTON_Y + 38) {
            if (event.x >= 560 && event.x < 676 && kind != VX_MESSAGE) {
                return 1;
            }
            if (event.x >= 692 && event.x < 808) {
                *accepted = 1;
                return 1;
            }
        }
    }
}

int vx_dialog_message(VxWindow *window, const char *title, const char *text)
{
    int accepted = 0;
    return vx_modal_run(window, VX_MESSAGE, title, text, NULL, 0, &accepted);
}

int vx_dialog_confirm(VxWindow *window, const char *title, const char *text,
                      int *accepted)
{
    return vx_modal_run(window, VX_CONFIRM, title, text, NULL, 0, accepted);
}

int vx_dialog_entry(VxWindow *window, const char *title, const char *prompt,
                    const char *initial, char *value, size_t value_size,
                    int *accepted)
{
    if (initial == NULL || value == NULL || value_size == 0
        || strlen(initial) >= value_size) {
        return 0;
    }
    memcpy(value, initial, strlen(initial) + 1);
    return vx_modal_run(window, VX_ENTRY, title, prompt, value, value_size,
                        accepted);
}

int vx_dialog_edit_text(VxWindow *window, const char *title,
                        const char *description, const char *initial,
                        char *value, size_t value_size, int *accepted)
{
    if (initial == NULL || value == NULL || value_size == 0
        || strlen(initial) >= value_size) {
        return 0;
    }
    memcpy(value, initial, strlen(initial) + 1);
    int result = vx_modal_run(window, VX_EDIT, title, description, value,
                              value_size, accepted);
    if (result && !*accepted) {
        value[0] = '\0';
    }
    return result;
}

static void vx_json_space(const char **cursor)
{
    while (**cursor == ' ' || **cursor == '\t' || **cursor == '\r'
           || **cursor == '\n') {
        ++*cursor;
    }
}

static int vx_json_string(const char **cursor, char **value)
{
    if (*(*cursor)++ != '"') {
        return 0;
    }
    char *output = malloc(strlen(*cursor) + 1);
    if (output == NULL) {
        return 0;
    }
    size_t used = 0;
    while (**cursor != '\0' && **cursor != '"') {
        unsigned char character = (unsigned char)*(*cursor)++;
        if (character == '\\') {
            character = (unsigned char)*(*cursor)++;
            switch (character) {
            case '"': case '\\': case '/': break;
            case 'n': character = '\n'; break;
            case 'r': character = '\r'; break;
            case 't': character = '\t'; break;
            default: free(output); return 0;
            }
        }
        output[used++] = (char)character;
    }
    if (*(*cursor)++ != '"') {
        free(output);
        return 0;
    }
    output[used] = '\0';
    *value = output;
    return 1;
}

static void vx_string_list_destroy(VxStringList *list)
{
    for (size_t index = 0; index < list->count; ++index) {
        free(list->items[index]);
    }
    free(list->items);
    memset(list, 0, sizeof(*list));
}

static int vx_parse_string_list(const char *json, VxStringList *list)
{
    const char *cursor = json;
    vx_json_space(&cursor);
    if (*cursor++ != '[') {
        return 0;
    }
    vx_json_space(&cursor);
    if (*cursor == ']') {
        ++cursor;
        vx_json_space(&cursor);
        return *cursor == '\0';
    }
    for (;;) {
        char *value = NULL;
        if (!vx_json_string(&cursor, &value)) {
            vx_string_list_destroy(list);
            return 0;
        }
        char **items = realloc(list->items, (list->count + 1) * sizeof(*items));
        if (items == NULL) {
            free(value);
            vx_string_list_destroy(list);
            return 0;
        }
        list->items = items;
        list->items[list->count++] = value;
        vx_json_space(&cursor);
        if (*cursor == ']') {
            ++cursor;
            vx_json_space(&cursor);
            return *cursor == '\0';
        }
        if (*cursor++ != ',') {
            vx_string_list_destroy(list);
            return 0;
        }
        vx_json_space(&cursor);
    }
}

static void vx_rows_destroy(VxDialogRows *rows)
{
    for (size_t index = 0; index < rows->count; ++index) {
        free(rows->items[index].first);
        free(rows->items[index].second);
    }
    free(rows->items);
    memset(rows, 0, sizeof(*rows));
}

static int vx_parse_rows(const char *json, VxDialogRows *rows)
{
    const char *cursor = json;
    vx_json_space(&cursor);
    if (*cursor++ != '[') {
        return 0;
    }
    vx_json_space(&cursor);
    if (*cursor == ']') {
        ++cursor;
        vx_json_space(&cursor);
        return *cursor == '\0';
    }
    for (;;) {
        char *first = NULL;
        char *second = NULL;
        if (*cursor++ != '[') {
            vx_rows_destroy(rows);
            return 0;
        }
        vx_json_space(&cursor);
        if (!vx_json_string(&cursor, &first)) {
            vx_rows_destroy(rows);
            return 0;
        }
        vx_json_space(&cursor);
        if (*cursor++ != ',') {
            free(first);
            vx_rows_destroy(rows);
            return 0;
        }
        vx_json_space(&cursor);
        if (!vx_json_string(&cursor, &second)) {
            free(first);
            vx_rows_destroy(rows);
            return 0;
        }
        vx_json_space(&cursor);
        if (*cursor++ != ']') {
            free(first);
            free(second);
            vx_rows_destroy(rows);
            return 0;
        }
        VxDialogRow *items = realloc(rows->items,
                                     (rows->count + 1) * sizeof(*items));
        if (items == NULL) {
            free(first);
            free(second);
            vx_rows_destroy(rows);
            return 0;
        }
        rows->items = items;
        rows->items[rows->count++] = (VxDialogRow){first, second};
        vx_json_space(&cursor);
        if (*cursor == ']') {
            ++cursor;
            vx_json_space(&cursor);
            return *cursor == '\0';
        }
        if (*cursor++ != ',') {
            vx_rows_destroy(rows);
            return 0;
        }
        vx_json_space(&cursor);
    }
}

static int vx_get_response(const char *button, char *response,
                           size_t response_size)
{
    const char *separator = strrchr(button, ':');
    if (separator == NULL || separator[1] == '\0') {
        return 0;
    }
    for (const char *cursor = separator + 1; *cursor != '\0'; ++cursor) {
        if (*cursor < '0' || *cursor > '9') {
            return 0;
        }
    }
    if (strlen(separator + 1) >= response_size) {
        return 0;
    }
    strcpy(response, separator + 1);
    return 1;
}

static void vx_draw_list(VxWindow *window, const char *title,
                         const char *description, const VxDialogRows *rows,
                         const VxStringList *buttons, size_t selected)
{
    vx_begin_frame(window, 0xf3f6fbu);
    vx_fill_rect(window, VX_LIST_X, VX_LIST_Y, VX_LIST_WIDTH, VX_LIST_HEIGHT,
                 0xffffffu);
    vx_draw_rect(window, VX_LIST_X, VX_LIST_Y, VX_LIST_WIDTH, VX_LIST_HEIGHT,
                 0xcbd5e2u);
    vx_draw_text(window, VX_LIST_X + 20, VX_LIST_Y + 34, 0x1b2b43u, title);
    vx_draw_lines(window, VX_LIST_X + 20, VX_LIST_Y + 62, 20, 2,
                  description, 0x52637au);
    int row_top = VX_LIST_Y + 108;
    int visible_rows = (VX_LIST_HEIGHT - 166) / VX_ROW_HEIGHT;
    size_t first = (selected / (size_t)visible_rows) * (size_t)visible_rows;
    for (int line = 0; line < visible_rows && first + (size_t)line < rows->count;
         ++line) {
        size_t index = first + (size_t)line;
        int y = row_top + line * VX_ROW_HEIGHT;
        vx_fill_rect(window, VX_LIST_X + 12, y, VX_LIST_WIDTH - 24,
                     VX_ROW_HEIGHT - 2, index == selected ? 0xdce9fbu : 0xffffffu);
        vx_draw_rect(window, VX_LIST_X + 12, y, VX_LIST_WIDTH - 24,
                     VX_ROW_HEIGHT - 2, 0xe2e8f0u);
        vx_draw_text(window, VX_LIST_X + 22, y + 23, 0x1b2b43u,
                     rows->items[index].first);
        vx_draw_text(window, VX_LIST_X + 380, y + 23, 0x65748au,
                     rows->items[index].second);
    }
    int shown = buttons->count > 5 ? 5 : (int)buttons->count;
    int button_x = VX_LIST_X + VX_LIST_WIDTH - 20 - shown * 124;
    for (int index = 0; index < shown; ++index) {
        vx_draw_button(window, button_x + index * 124,
                       VX_LIST_Y + VX_LIST_HEIGHT - 58,
                       buttons->items[index]);
    }
    vx_present(window);
}

int vx_dialog_list(VxWindow *window, const char *title, const char *description,
                   const char *buttons_json, const char *rows_json,
                   char *selected, size_t selected_size, char *response,
                   size_t response_size)
{
    if (window == NULL || selected == NULL || selected_size == 0
        || response == NULL || response_size == 0) {
        return 0;
    }
    VxStringList buttons = {0};
    VxDialogRows rows = {0};
    if (!vx_parse_string_list(buttons_json, &buttons) || buttons.count == 0
        || !vx_parse_rows(rows_json, &rows)) {
        vx_string_list_destroy(&buttons);
        vx_rows_destroy(&rows);
        return 0;
    }
    selected[0] = '\0';
    response[0] = '\0';
    size_t index = 0;
    int button_count = buttons.count > 5 ? 5 : (int)buttons.count;
    int button_x = VX_LIST_X + VX_LIST_WIDTH - 20 - button_count * 124;
    vx_draw_list(window, title, description, &rows, &buttons, index);
    for (;;) {
        VxEvent event;
        if (!vx_window_next_event(window, &event, 1)) {
            continue;
        }
        if (event.type == VX_EVENT_EXPOSE || event.type == VX_EVENT_RESIZE) {
            vx_draw_list(window, title, description, &rows, &buttons, index);
            continue;
        }
        if (event.type == VX_EVENT_CLOSE
            || (event.type == VX_EVENT_KEY_DOWN && event.key == VX_KEY_ESCAPE)) {
            snprintf(response, response_size, "%s", buttons.count > 1 ? "1" : "0");
            break;
        }
        if (event.type == VX_EVENT_KEY_DOWN
            && (event.key == 0xff52u || event.key == 0xff54u)
            && rows.count > 0) {
            if (event.key == 0xff52u && index > 0) {
                --index;
            } else if (event.key == 0xff54u && index + 1 < rows.count) {
                ++index;
            }
            vx_draw_list(window, title, description, &rows, &buttons, index);
            continue;
        }
        if (event.type == VX_EVENT_KEY_DOWN && event.key == VX_KEY_RETURN) {
            (void)vx_get_response(buttons.items[0], response, response_size);
            if (index < rows.count
                && strlen(rows.items[index].first) < selected_size) {
                strcpy(selected, rows.items[index].first);
            }
            break;
        }
        if (event.type != VX_EVENT_POINTER_UP || event.key != 1) {
            continue;
        }
        int row_top = VX_LIST_Y + 108;
        int row_bottom = row_top
            + ((VX_LIST_HEIGHT - 166) / VX_ROW_HEIGHT) * VX_ROW_HEIGHT;
        if (event.x >= VX_LIST_X + 12 && event.x < VX_LIST_X + VX_LIST_WIDTH - 12
            && event.y >= row_top && event.y < row_bottom && rows.count > 0) {
            size_t first = (index / 10) * 10;
            size_t clicked = first + (size_t)((event.y - row_top) / VX_ROW_HEIGHT);
            if (clicked < rows.count) {
                index = clicked;
                vx_draw_list(window, title, description, &rows, &buttons, index);
            }
            continue;
        }
        int button_y = VX_LIST_Y + VX_LIST_HEIGHT - 58;
        int button_index = (event.x - button_x) / 124;
        if (event.y >= button_y && event.y < button_y + 38
            && event.x >= button_x && button_index >= 0
            && button_index < button_count
            && (event.x - button_x) % 124 < 116) {
            (void)vx_get_response(buttons.items[button_index], response,
                                  response_size);
            if (index < rows.count
                && strlen(rows.items[index].first) < selected_size) {
                strcpy(selected, rows.items[index].first);
            }
            break;
        }
    }
    vx_string_list_destroy(&buttons);
    vx_rows_destroy(&rows);
    return 1;
}

int vx_dialog_text_file(VxWindow *window, const char *title, const char *path,
                        const char *buttons_json, char *response,
                        size_t response_size)
{
#ifdef _WIN32
    (void)window;
    (void)title;
    (void)path;
    (void)buttons_json;
    (void)response;
    (void)response_size;
    return 0;
#else
    if (window == NULL || title == NULL || path == NULL || response == NULL
        || response_size == 0) {
        return 0;
    }
    int descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (descriptor < 0) {
        return 0;
    }
    struct stat info;
    if (fstat(descriptor, &info) != 0 || !S_ISREG(info.st_mode)
        || info.st_size < 0 || info.st_size > 2 * 1024 * 1024) {
        close(descriptor);
        return 0;
    }
    size_t capacity = (size_t)info.st_size + 1;
    char *contents = malloc(capacity);
    if (contents == NULL) {
        close(descriptor);
        return 0;
    }
    size_t used = 0;
    while (used < capacity - 1) {
        ssize_t count = read(descriptor, contents + used, capacity - 1 - used);
        if (count < 0 && errno == EINTR) {
            continue;
        }
        if (count <= 0) {
            if (count < 0) {
                free(contents);
                close(descriptor);
                return 0;
            }
            break;
        }
        used += (size_t)count;
    }
    close(descriptor);
    contents[used] = '\0';
    VxStringList buttons = {0};
    if (!vx_parse_string_list(buttons_json, &buttons) || buttons.count == 0) {
        free(contents);
        vx_string_list_destroy(&buttons);
        return 0;
    }
    int shown = buttons.count > 5 ? 5 : (int)buttons.count;
    int button_x = 930 - shown * 124;
    int button_y = 584;
    response[0] = '\0';
    for (;;) {
        vx_begin_frame(window, 0xf3f6fbu);
        vx_fill_rect(window, 50, 42, 880, 600, 0xffffffu);
        vx_draw_rect(window, 50, 42, 880, 600, 0xcbd5e2u);
        vx_draw_text(window, 72, 78, 0x1b2b43u, title);
        vx_draw_lines(window, 72, 108, 20, 23, contents, 0x52637au);
        for (int index = 0; index < shown; ++index) {
            vx_draw_button(window, button_x + index * 124, button_y,
                           buttons.items[index]);
        }
        vx_present(window);
        VxEvent event;
        if (!vx_window_next_event(window, &event, 1)) {
            continue;
        }
        if (event.type == VX_EVENT_EXPOSE || event.type == VX_EVENT_RESIZE) {
            continue;
        }
        if (event.type == VX_EVENT_CLOSE
            || (event.type == VX_EVENT_KEY_DOWN && event.key == VX_KEY_ESCAPE)) {
            snprintf(response, response_size, "1");
            break;
        }
        if (event.type == VX_EVENT_KEY_DOWN && event.key == VX_KEY_RETURN) {
            (void)vx_get_response(buttons.items[0], response, response_size);
            break;
        }
        if (event.type == VX_EVENT_POINTER_UP && event.key == 1
            && event.y >= button_y && event.y < button_y + 38
            && event.x >= button_x) {
            int index = (event.x - button_x) / 124;
            if (index >= 0 && index < shown && (event.x - button_x) % 124 < 116) {
                (void)vx_get_response(buttons.items[index], response, response_size);
                break;
            }
        }
    }
    free(contents);
    vx_string_list_destroy(&buttons);
    return 1;
#endif
}
