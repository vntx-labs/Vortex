#ifndef VORTEX_ACTIONS_H
#define VORTEX_ACTIONS_H

#include "vortex_language.h"
#include "vortex_ui.h"

typedef struct {
    VxWindow *window;
    int error_was_shown;
} VxActionContext;

int vx_actions_handle(void *context, const char *action, size_t argument_count,
                      const char *const *arguments, VxVariables *variables,
                      char *error, size_t error_size);

#endif
