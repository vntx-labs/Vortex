#include "vortex_actions.h"

#include <stdio.h>
#include <string.h>

static int check(int condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "test_actions: %s\n", message);
        return 0;
    }
    return 1;
}

int main(void)
{
    VxVariables *variables = vx_variables_create();
    if (!check(variables != NULL, "variable table allocation")) {
        return 1;
    }
    VxActionContext context = {0};
    char error[256] = {0};
    int status = vx_actions_handle(&context, "ui.confirm", 0, NULL, variables,
                                   error, sizeof(error));
    int passed = check(status == 2 && strstr(error, "erwartet 3 Argument") != NULL,
                       "argument count is validated");

    memset(error, 0, sizeof(error));
    status = vx_actions_handle(&context, "not.registered", 0, NULL, variables,
                               error, sizeof(error));
    passed &= check(status == 2
                    && strstr(error, "not.registered") != NULL
                    && strstr(error, "nicht implementiert") != NULL,
                    "unimplemented actions fail explicitly");

    memset(error, 0, sizeof(error));
    const char *port_arguments[] = {"443"};
    status = vx_actions_handle(&context, "process.validate_port", 1,
                               port_arguments, variables, error, sizeof(error));
    passed &= check(status == 0, "action registry dispatches native process actions");

    memset(error, 0, sizeof(error));
    const char *message_arguments[] = {"Hinweis", "Text"};
    status = vx_actions_handle(&context, "ui.info", 2, message_arguments, variables,
                               error, sizeof(error));
    passed &= check(status == 1 && strstr(error, "nicht angezeigt") != NULL,
                    "UI failures are surfaced");
    vx_variables_destroy(variables);
    return passed ? 0 : 1;
}
