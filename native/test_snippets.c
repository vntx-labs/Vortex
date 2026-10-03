#define _POSIX_C_SOURCE 200809L

#include "vortex_snippets.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int check(int condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "test_snippets: %s\n", message);
        return 0;
    }
    return 1;
}

static int call_action(const char *action, size_t count,
                       const char *const *arguments, VxVariables *variables,
                       char *error, size_t error_size)
{
    int handled = 0;
    int status = vx_snippets_action(action, count, arguments, variables,
                                    &handled, error, error_size);
    if (!handled) {
        snprintf(error, error_size, "action not handled: %s", action);
        return 255;
    }
    return status;
}

int main(void)
{
    char home_template[] = "/tmp/vortex-snippets-XXXXXX";
    char *home = mkdtemp(home_template);
    if (!check(home != NULL, "temporary HOME directory")) {
        return 1;
    }
    if (setenv("HOME", home, 1) != 0) {
        rmdir(home);
        return 1;
    }
    VxVariables *variables = vx_variables_create();
    if (!check(variables != NULL, "variable table allocation")) {
        rmdir(home);
        return 1;
    }
    char error[512] = {0};
    int passed = call_action("snippets.initialize", 0, NULL, variables,
                             error, sizeof(error)) == 0;

    const char *list_args[] = {"items"};
    int status = call_action("snippets.list", 1, list_args, variables, error,
                             sizeof(error));
    passed &= check(status == 0
                    && strcmp(vx_variable_get(variables, "items"), "[]") == 0,
                    "empty store serializes as JSON array");

    const char *save_args[] = {"new", "Café \"One\"", "line 1\nline 2\tend"};
    status = call_action("snippets.save", 3, save_args, variables, error,
                         sizeof(error));
    passed &= check(status == 0, "append UTF-8 snippet");

    status = call_action("snippets.list", 1, list_args, variables, error,
                         sizeof(error));
    const char *items = vx_variable_get(variables, "items");
    passed &= check(status == 0 && items != NULL
                    && strstr(items, "[1,\"Café \\\"One\\\"\"]") != NULL,
                    "snippet title is JSON escaped and UTF-8 preserved");

    const char *get_args[] = {"title", "body", "1"};
    status = call_action("snippets.get", 3, get_args, variables, error,
                         sizeof(error));
    passed &= check(status == 0
                    && strcmp(vx_variable_get(variables, "title"), "Café \"One\"") == 0
                    && strcmp(vx_variable_get(variables, "body"), "line 1\nline 2\tend") == 0,
                    "snippet round-trips UTF-8, newline, and tab");

    const char *update_args[] = {"1", "Updated", "new body"};
    status = call_action("snippets.save", 3, update_args, variables, error,
                         sizeof(error));
    passed &= check(status == 0, "update existing snippet");
    const char *delete_args[] = {"1"};
    status = call_action("snippets.delete", 1, delete_args, variables, error,
                         sizeof(error));
    passed &= check(status == 0, "delete existing snippet");
    status = call_action("snippets.list", 1, list_args, variables, error,
                         sizeof(error));
    passed &= check(status == 0
                    && strcmp(vx_variable_get(variables, "items"), "[]") == 0,
                    "deleting last snippet restores empty store");

    char store_path[1024];
    snprintf(store_path, sizeof(store_path), "%s/.local/share/velos/snippets.tsv", home);
    unlink(store_path);
    char directory[1024];
    snprintf(directory, sizeof(directory), "%s/.local/share/velos", home);
    rmdir(directory);
    snprintf(directory, sizeof(directory), "%s/.local/share", home);
    rmdir(directory);
    snprintf(directory, sizeof(directory), "%s/.local", home);
    rmdir(directory);
    rmdir(home);
    vx_variables_destroy(variables);
    return passed ? 0 : 1;
}
