#include "vortex_language.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int increments;
    int recorded_error;
} TestContext;

static int test_action(void *opaque, const char *action, size_t count,
                       const char *const *arguments, VxVariables *variables,
                       char *error, size_t error_size)
{
    TestContext *context = opaque;
    if (strcmp(action, "test.set") == 0 && count == 2) {
        return vx_variable_set(variables, arguments[0], arguments[1]) ? 0 : 1;
    }
    if (strcmp(action, "test.bump") == 0 && count == 0) {
        const char *value = vx_variable_get(variables, "count");
        if (value == NULL) {
            snprintf(error, error_size, "count missing");
            return 1;
        }
        char next[32];
        snprintf(next, sizeof(next), "%d", atoi(value) + 1);
        ++context->increments;
        return vx_variable_set(variables, "count", next) ? 0 : 1;
    }
    if (strcmp(action, "test.fail") == 0 && count == 0) {
        snprintf(error, error_size, "expected failure");
        return 1;
    }
    if (strcmp(action, "test.record") == 0 && count == 1
        && strcmp(arguments[0], "expected failure") == 0) {
        ++context->recorded_error;
        return 0;
    }
    snprintf(error, error_size, "unexpected test action");
    return 2;
}

int main(void)
{
    const char *program =
        "call test.set count 0\n"
        "loop\n"
        "    if equals count 3\n"
        "        break\n"
        "    end\n"
        "    call test.bump\n"
        "end\n"
        "call test.fail\n"
        "if failed last\n"
        "    call test.record \"${last_error}\"\n"
        "end\n"
        "if equals count 3\n"
        "    return 0\n"
        "else\n"
        "    return 9\n"
        "end\n";
    char error[256] = {0};
    TestContext context = {0};
    int status = vx_script_run(program, test_action, &context, error, sizeof(error));
    if (status != 0 || context.increments != 3 || context.recorded_error != 1) {
        fprintf(stderr, "DSL runtime test failed: status=%d increments=%d errors=%d: %s\n",
                status, context.increments, context.recorded_error, error);
        return 1;
    }
    puts("C DSL runtime OK");
    return 0;
}
