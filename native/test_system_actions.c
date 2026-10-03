#define _POSIX_C_SOURCE 200809L

#include "vortex_system_actions.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int check(int condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "test_system_actions: %s\n", message);
        return 0;
    }
    return 1;
}

static int call_action(const char *action, size_t count,
                       const char *const *arguments, VxVariables *variables,
                       char *error, size_t error_size)
{
    int handled = 0;
    int status = vx_system_action(action, count, arguments, variables, &handled,
                                  error, error_size);
    if (!handled) {
        snprintf(error, error_size, "action not handled: %s", action);
        return 255;
    }
    return status;
}

int main(void)
{
    char directory_template[] = "/tmp/vortex-actions-XXXXXX";
    char *directory = mkdtemp(directory_template);
    if (!check(directory != NULL, "temporary directory")) {
        return 1;
    }
    char source[512];
    char child_directory[512];
    char output_path[512];
    char log_path[512];
    snprintf(source, sizeof(source), "%s/source", directory);
    snprintf(child_directory, sizeof(child_directory), "%s/source/child", directory);
    snprintf(output_path, sizeof(output_path), "%s/output.txt", directory);
    snprintf(log_path, sizeof(log_path), "%s/log.txt", directory);
    int passed = check(mkdir(source, 0700) == 0, "create source directory");
    passed &= check(mkdir(child_directory, 0700) == 0, "create nested directory");

    VxVariables *variables = vx_variables_create();
    passed &= check(variables != NULL, "variable table allocation");
    char error[512] = {0};
    vx_variable_set(variables, "source", source);
    vx_variable_set(variables, "nested", child_directory);

    const char *overlap_args[] = {"source", "nested"};
    int status = call_action("fs.assert_disjoint", 2, overlap_args, variables,
                             error, sizeof(error));
    passed &= check(status == 1 && strstr(error, "ineinander") != NULL,
                    "nested backup folders are rejected");

    const char *valid_port[] = {"65535"};
    status = call_action("process.validate_port", 1, valid_port, variables,
                         error, sizeof(error));
    passed &= check(status == 0, "highest valid TCP port is accepted");
    const char *invalid_port[] = {"65536"};
    status = call_action("process.validate_port", 1, invalid_port, variables,
                         error, sizeof(error));
    passed &= check(status == 1, "out-of-range TCP port is rejected");

    vx_variable_set(variables, "path", source);
    const char *realpath_args[] = {"path"};
    status = call_action("fs.realpath", 1, realpath_args, variables, error,
                         sizeof(error));
    passed &= check(status == 0
                    && strcmp(vx_variable_get(variables, "path"), source) == 0,
                    "fs.realpath stores the canonical path");

    const char *temp_args[] = {"log", "vortex-test."};
    status = call_action("fs.temp", 2, temp_args, variables, error, sizeof(error));
    const char *log_path_value = vx_variable_get(variables, "log");
    passed &= check(status == 0 && log_path_value != NULL,
                    "temporary file path is assigned");
    if (log_path_value != NULL) {
        snprintf(log_path, sizeof(log_path), "%s", log_path_value);
        FILE *log = fopen(log_path, "w");
        passed &= check(log != NULL, "temporary log is writable");
        if (log != NULL) {
            fputs("first\nsecond\nthird\n", log);
            fclose(log);
        }
    }
    const char *tail_args[] = {"log", "tail", "2"};
    status = call_action("fs.tail", 3, tail_args, variables, error, sizeof(error));
    passed &= check(status == 0
                    && strcmp(vx_variable_get(variables, "tail"), "second\nthird\n") == 0,
                    "fs.tail returns exactly the requested trailing lines");

    vx_variable_set(variables, "output", output_path);
    const char *capture_args[] = {"output", "2", "printf", "captured"};
    status = call_action("process.capture", 4, capture_args, variables, error,
                         sizeof(error));
    FILE *output = fopen(output_path, "r");
    char contents[64] = {0};
    if (output != NULL) {
        size_t read_count = fread(contents, 1, sizeof(contents) - 1, output);
        contents[read_count] = '\0';
        fclose(output);
    }
    passed &= check(status == 0 && strcmp(contents, "captured") == 0,
                    "process.capture runs argv directly and captures output");

    const char *failed_command[] = {"output", "2", "false"};
    status = call_action("process.capture", 3, failed_command, variables, error,
                         sizeof(error));
    passed &= check(status != 0, "process.capture propagates nonzero exit status");

    const char *timeout_args[] = {"output", "0.05", "sleep", "1"};
    status = call_action("process.capture", 4, timeout_args, variables, error,
                         sizeof(error));
    passed &= check(status == 124 && strstr(error, "Zeitlimit") != NULL,
                    "process.capture enforces its timeout");

    const char *remove_args[] = {"log"};
    status = call_action("fs.remove", 1, remove_args, variables, error, sizeof(error));
    passed &= check(status == 0 && access(log_path, F_OK) != 0,
                    "fs.remove removes the temporary file");
    unlink(output_path);
    rmdir(child_directory);
    rmdir(source);
    rmdir(directory);
    vx_variables_destroy(variables);
    return passed ? 0 : 1;
}
