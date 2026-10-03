#ifndef VORTEX_SNIPPETS_H
#define VORTEX_SNIPPETS_H

#include "vortex_language.h"

int vx_snippets_action(const char *action, size_t argument_count,
                       const char *const *arguments, VxVariables *variables,
                       int *handled, char *error, size_t error_size);

#endif
