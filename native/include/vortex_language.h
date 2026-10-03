#ifndef VORTEX_LANGUAGE_H
#define VORTEX_LANGUAGE_H

#include <stddef.h>

typedef struct VxVariables VxVariables;

typedef int (*VxActionHandler)(void *context, const char *action,
                              size_t argument_count, const char *const *arguments,
                              VxVariables *variables, char *error,
                              size_t error_size);

VxVariables *vx_variables_create(void);
void vx_variables_destroy(VxVariables *variables);
const char *vx_variable_get(const VxVariables *variables, const char *name);
int vx_variable_set(VxVariables *variables, const char *name, const char *value);

int vx_script_run(const char *source, VxActionHandler handler, void *context,
                  char *error, size_t error_size);
int vx_script_check_file(const char *path, char *error, size_t error_size);

#endif
