#ifndef VORTEX_SYSTEM_ACTIONS_H
#define VORTEX_SYSTEM_ACTIONS_H

#include "vortex_language.h"

int vx_system_action(const char *action, size_t argument_count,
                     const char *const *arguments, VxVariables *variables,
                     int *handled, char *error, size_t error_size);

#endif
