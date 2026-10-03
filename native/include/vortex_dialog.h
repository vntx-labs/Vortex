#ifndef VORTEX_DIALOG_H
#define VORTEX_DIALOG_H

#include <stddef.h>

#include "vortex_ui.h"

int vx_dialog_message(VxWindow *window, const char *title, const char *text);
int vx_dialog_confirm(VxWindow *window, const char *title, const char *text,
                      int *accepted);
int vx_dialog_entry(VxWindow *window, const char *title, const char *prompt,
                   const char *initial, char *value, size_t value_size,
                   int *accepted);
int vx_dialog_list(VxWindow *window, const char *title, const char *description,
                   const char *buttons_json, const char *rows_json,
                   char *selected, size_t selected_size, char *response,
                   size_t response_size);
int vx_dialog_text_file(VxWindow *window, const char *title, const char *path,
                        const char *buttons_json, char *response,
                        size_t response_size);
int vx_dialog_edit_text(VxWindow *window, const char *title,
                        const char *description, const char *initial,
                        char *value, size_t value_size, int *accepted);

#endif
