#define _POSIX_C_SOURCE 200809L

#include "vortex_language.h"

#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { VX_MAX_LINE = 16384, VX_MAX_SOURCE = 1024 * 1024 };

typedef struct {
    char **items;
    size_t count;
} VxTokens;

typedef struct {
    VxTokens tokens;
    size_t line;
    size_t matching;
    size_t alternate;
} VxInstruction;

typedef struct {
    VxInstruction *items;
    size_t count;
} VxProgram;

typedef struct {
    char *name;
    char *value;
} VxVariable;

struct VxVariables {
    VxVariable *items;
    size_t count;
};

static void vx_tokens_destroy(VxTokens *tokens)
{
    for (size_t index = 0; index < tokens->count; ++index) {
        free(tokens->items[index]);
    }
    free(tokens->items);
    memset(tokens, 0, sizeof(*tokens));
}

static int vx_tokenize(const char *line, VxTokens *tokens)
{
    const char *cursor = line;
    while (*cursor) {
        while (isspace((unsigned char)*cursor)) {
            ++cursor;
        }
        if (*cursor == '\0') {
            break;
        }
        char quote = '\0';
        if (*cursor == '\'' || *cursor == '"') {
            quote = *cursor++;
        }
        size_t capacity = strlen(cursor) + 1;
        char *word = malloc(capacity);
        if (word == NULL) {
            return 0;
        }
        size_t length = 0;
        int closed = quote == '\0';
        while (*cursor) {
            if (quote != '\0' && *cursor == quote) {
                ++cursor;
                closed = 1;
                break;
            }
            if (quote == '\0' && isspace((unsigned char)*cursor)) {
                break;
            }
            if (*cursor == '\\' && cursor[1] != '\0'
                && (quote == '\0' || cursor[1] == quote || cursor[1] == '\\')) {
                ++cursor;
            }
            word[length++] = *cursor++;
        }
        if (!closed || (quote != '\0' && *cursor != '\0'
                        && !isspace((unsigned char)*cursor))) {
            free(word);
            return 0;
        }
        word[length] = '\0';
        char **expanded = realloc(tokens->items, (tokens->count + 1) * sizeof(*expanded));
        if (expanded == NULL) {
            free(word);
            return 0;
        }
        tokens->items = expanded;
        tokens->items[tokens->count++] = word;
    }
    return 1;
}

static int vx_program_add(VxProgram *program, VxTokens *tokens, size_t line)
{
    VxInstruction *expanded = realloc(
        program->items, (program->count + 1) * sizeof(*expanded)
    );
    if (expanded == NULL) {
        return 0;
    }
    program->items = expanded;
    program->items[program->count++] = (VxInstruction){
        .tokens = *tokens, .line = line, .matching = SIZE_MAX,
        .alternate = SIZE_MAX
    };
    memset(tokens, 0, sizeof(*tokens));
    return 1;
}

static int vx_is(const VxInstruction *instruction, const char *value)
{
    return instruction->tokens.count > 0
        && strcmp(instruction->tokens.items[0], value) == 0;
}

static int vx_parse(const char *source, VxProgram *program,
                    char *error, size_t error_size)
{
    if (strlen(source) > VX_MAX_SOURCE) {
        snprintf(error, error_size, "Modulskript überschreitet das Limit von 1 MB.");
        return 0;
    }
    char *copy = strdup(source);
    if (copy == NULL) {
        snprintf(error, error_size, "Nicht genug Speicher für Modulskript.");
        return 0;
    }
    size_t *stack = NULL;
    size_t stack_count = 0;
    char *save = NULL;
    char *line = strtok_r(copy, "\n", &save);
    size_t line_number = 0;
    int valid = 1;
    while (line != NULL) {
        ++line_number;
        size_t line_length = strlen(line);
        if (line_length > 0 && line[line_length - 1] == '\r') {
            line[--line_length] = '\0';
        }
        if (line_length > VX_MAX_LINE) {
            snprintf(error, error_size, "Zeile %zu: Zeile ist zu lang.", line_number);
            valid = 0;
            break;
        }
        const char *start = line;
        while (isspace((unsigned char)*start)) {
            ++start;
        }
        if (*start == '\0' || *start == '#') {
            line = strtok_r(NULL, "\n", &save);
            continue;
        }
        VxTokens tokens = {0};
        if (!vx_tokenize(start, &tokens) || tokens.count == 0) {
            vx_tokens_destroy(&tokens);
            snprintf(error, error_size, "Zeile %zu: ungültige Anführungszeichen oder Speicherfehler.", line_number);
            valid = 0;
            break;
        }
        VxInstruction instruction = {.tokens = tokens, .line = line_number};
        const char *operation = tokens.items[0];
        size_t closing_opening = SIZE_MAX;
        if (strcmp(operation, "if") == 0) {
            if (!((tokens.count == 3
                   && (strcmp(tokens.items[1], "empty") == 0
                       || strcmp(tokens.items[1], "failed") == 0
                       || strcmp(tokens.items[1], "declined") == 0))
                  || (tokens.count == 4 && strcmp(tokens.items[1], "equals") == 0))) {
                vx_tokens_destroy(&tokens);
                snprintf(error, error_size, "Zeile %zu: ungültige if-Bedingung.", line_number);
                valid = 0;
                break;
            }
            if (strcmp(tokens.items[1], "failed") == 0
                && strcmp(tokens.items[2], "last") != 0) {
                vx_tokens_destroy(&tokens);
                snprintf(error, error_size, "Zeile %zu: 'if failed' erwartet 'last'.", line_number);
                valid = 0;
                break;
            }
        } else if (strcmp(operation, "loop") == 0) {
            if (tokens.count != 1) {
                vx_tokens_destroy(&tokens);
                snprintf(error, error_size, "Zeile %zu: 'loop' akzeptiert keine Argumente.", line_number);
                valid = 0;
                break;
            }
        } else if (strcmp(operation, "break") == 0) {
            if (tokens.count != 1) {
                vx_tokens_destroy(&tokens);
                snprintf(error, error_size, "Zeile %zu: 'break' akzeptiert keine Argumente.", line_number);
                valid = 0;
                break;
            }
            int inside_loop = 0;
            for (size_t index = stack_count; index > 0; --index) {
                if (vx_is(&program->items[stack[index - 1]], "loop")) {
                    inside_loop = 1;
                    break;
                }
            }
            if (!inside_loop) {
                vx_tokens_destroy(&tokens);
                snprintf(error, error_size, "Zeile %zu: 'break' muss in einer Schleife stehen.", line_number);
                valid = 0;
                break;
            }
        } else if (strcmp(operation, "else") == 0) {
            if (tokens.count != 1 || stack_count == 0
                || !vx_is(&program->items[stack[stack_count - 1]], "if")
                || program->items[stack[stack_count - 1]].alternate != SIZE_MAX) {
                vx_tokens_destroy(&tokens);
                snprintf(error, error_size, "Zeile %zu: unerwartetes 'else'.", line_number);
                valid = 0;
                break;
            }
            program->items[stack[stack_count - 1]].alternate = program->count;
        } else if (strcmp(operation, "end") == 0) {
            if (tokens.count != 1 || stack_count == 0) {
                vx_tokens_destroy(&tokens);
                snprintf(error, error_size, "Zeile %zu: unerwartetes 'end'.", line_number);
                valid = 0;
                break;
            }
            closing_opening = stack[stack_count - 1];
        } else if (strcmp(operation, "call") == 0) {
            if (tokens.count < 2) {
                vx_tokens_destroy(&tokens);
                snprintf(error, error_size, "Zeile %zu: 'call' benötigt eine Aktion.", line_number);
                valid = 0;
                break;
            }
        } else if (strcmp(operation, "return") == 0) {
            if (tokens.count > 2) {
                vx_tokens_destroy(&tokens);
                snprintf(error, error_size, "Zeile %zu: 'return' akzeptiert höchstens einen Status.", line_number);
                valid = 0;
                break;
            }
            if (tokens.count == 2) {
                char *end = NULL;
                long status = strtol(tokens.items[1], &end, 10);
                if (end == tokens.items[1] || *end != '\0' || status < 0 || status > 255) {
                    vx_tokens_destroy(&tokens);
                    snprintf(error, error_size, "Zeile %zu: ungültiger Rückgabestatus.", line_number);
                    valid = 0;
                    break;
                }
            }
        } else {
            vx_tokens_destroy(&tokens);
            snprintf(error, error_size, "Zeile %zu: unbekannter Befehl '%s'.", line_number, operation);
            valid = 0;
            break;
        }
        if (!vx_program_add(program, &instruction.tokens, line_number)) {
            vx_tokens_destroy(&instruction.tokens);
            snprintf(error, error_size, "Zeile %zu: nicht genug Speicher.", line_number);
            valid = 0;
            break;
        }
        if (strcmp(operation, "if") == 0 || strcmp(operation, "loop") == 0) {
            size_t *expanded = realloc(stack, (stack_count + 1) * sizeof(*expanded));
            if (expanded == NULL) {
                snprintf(error, error_size, "Zeile %zu: nicht genug Speicher.", line_number);
                valid = 0;
                break;
            }
            stack = expanded;
            stack[stack_count++] = program->count - 1;
        } else if (strcmp(operation, "end") == 0) {
            size_t opening = closing_opening;
            program->items[opening].matching = program->count - 1;
            program->items[program->count - 1].matching = opening;
            --stack_count;
        }
        line = strtok_r(NULL, "\n", &save);
    }
    if (valid && stack_count != 0) {
        snprintf(error, error_size, "Zeile %zu: Block wird nicht mit 'end' geschlossen.",
                 program->items[stack[stack_count - 1]].line);
        valid = 0;
    }
    free(stack);
    free(copy);
    return valid;
}

VxVariables *vx_variables_create(void)
{
    return calloc(1, sizeof(VxVariables));
}

void vx_variables_destroy(VxVariables *variables)
{
    if (variables == NULL) {
        return;
    }
    for (size_t index = 0; index < variables->count; ++index) {
        free(variables->items[index].name);
        free(variables->items[index].value);
    }
    free(variables->items);
    free(variables);
}

const char *vx_variable_get(const VxVariables *variables, const char *name)
{
    for (size_t index = 0; variables != NULL && index < variables->count; ++index) {
        if (strcmp(variables->items[index].name, name) == 0) {
            return variables->items[index].value;
        }
    }
    return NULL;
}

int vx_variable_set(VxVariables *variables, const char *name, const char *value)
{
    if (variables == NULL || name == NULL || value == NULL) {
        return 0;
    }
    char *new_value = strdup(value);
    if (new_value == NULL) {
        return 0;
    }
    for (size_t index = 0; index < variables->count; ++index) {
        if (strcmp(variables->items[index].name, name) == 0) {
            free(variables->items[index].value);
            variables->items[index].value = new_value;
            return 1;
        }
    }
    char *new_name = strdup(name);
    VxVariable *expanded = realloc(
        variables->items, (variables->count + 1) * sizeof(*expanded)
    );
    if (new_name == NULL || expanded == NULL) {
        free(new_name);
        free(new_value);
        return 0;
    }
    variables->items = expanded;
    variables->items[variables->count++] = (VxVariable){new_name, new_value};
    return 1;
}

static char *vx_expand(const char *input, const VxVariables *variables,
                       size_t line, char *error, size_t error_size)
{
    size_t capacity = strlen(input) + 1;
    char *result = malloc(capacity);
    if (result == NULL) {
        return NULL;
    }
    size_t length = 0;
    for (size_t index = 0; input[index] != '\0';) {
        if (input[index] != '$' || input[index + 1] != '{') {
            result[length++] = input[index++];
            continue;
        }
        size_t start = index + 2;
        size_t end = start;
        while (input[end] != '\0' && input[end] != '}') {
            ++end;
        }
        if (input[end] != '}' || end == start) {
            snprintf(error, error_size, "Zeile %zu: ungültige Variableninterpolation.", line);
            free(result);
            return NULL;
        }
        char *name = strndup(input + start, end - start);
        const char *value = name == NULL ? NULL : vx_variable_get(variables, name);
        if (value == NULL) {
            snprintf(error, error_size, "Zeile %zu: unbekannte Variable '%s'.",
                     line, name == NULL ? "" : name);
            free(name);
            free(result);
            return NULL;
        }
        size_t needed = length + strlen(value) + strlen(input + end + 1) + 1;
        if (needed > capacity) {
            char *expanded = realloc(result, needed);
            if (expanded == NULL) {
                free(name);
                free(result);
                return NULL;
            }
            result = expanded;
            capacity = needed;
        }
        size_t value_length = strlen(value);
        memcpy(result + length, value, value_length);
        length += value_length;
        index = end + 1;
        free(name);
    }
    result[length] = '\0';
    return result;
}

static void vx_program_destroy(VxProgram *program)
{
    for (size_t index = 0; index < program->count; ++index) {
        vx_tokens_destroy(&program->items[index].tokens);
    }
    free(program->items);
    memset(program, 0, sizeof(*program));
}

int vx_script_run(const char *source, VxActionHandler handler, void *context,
                  char *error, size_t error_size)
{
    VxProgram program = {0};
    if (!vx_parse(source, &program, error, error_size)) {
        vx_program_destroy(&program);
        return 2;
    }
    VxVariables *variables = vx_variables_create();
    if (variables == NULL) {
        vx_program_destroy(&program);
        snprintf(error, error_size, "Nicht genug Speicher für DSL-Variablen.");
        return 1;
    }
    size_t *loops = malloc((program.count + 1) * sizeof(*loops));
    if (loops == NULL) {
        vx_variables_destroy(variables);
        vx_program_destroy(&program);
        snprintf(error, error_size, "Nicht genug Speicher für DSL-Schleifen.");
        return 1;
    }
    size_t loop_count = 0;
    int last_status = 0;
    size_t pc = 0;
    while (pc < program.count) {
        VxInstruction *instruction = &program.items[pc];
        const char *operation = instruction->tokens.items[0];
        if (strcmp(operation, "if") == 0) {
            const char *kind = instruction->tokens.items[1];
            const char *name = instruction->tokens.items[2];
            const char *value = vx_variable_get(variables, name);
            int matched = 0;
            if (strcmp(kind, "failed") == 0) {
                matched = last_status != 0;
            } else if (strcmp(kind, "empty") == 0) {
                if (value == NULL) {
                    snprintf(error, error_size, "Zeile %zu: unbekannte Variable '%s'.",
                             instruction->line, name);
                    last_status = 2;
                    break;
                }
                matched = value[0] == '\0';
            } else if (strcmp(kind, "declined") == 0) {
                if (value == NULL) {
                    snprintf(error, error_size, "Zeile %zu: unbekannte Variable '%s'.",
                             instruction->line, name);
                    last_status = 2;
                    break;
                }
                matched = strcmp(value, "yes") != 0 && strcmp(value, "0") != 0;
            } else if (strcmp(kind, "equals") == 0) {
                if (value == NULL) {
                    snprintf(error, error_size, "Zeile %zu: unbekannte Variable '%s'.",
                             instruction->line, name);
                    last_status = 2;
                    break;
                }
                matched = strcmp(value, instruction->tokens.items[3]) == 0;
            }
            if (!matched) {
                if (instruction->alternate != SIZE_MAX) {
                    pc = instruction->alternate + 1;
                } else {
                    pc = instruction->matching + 1;
                }
            } else {
                ++pc;
            }
            continue;
        }
        if (strcmp(operation, "else") == 0) {
            size_t opening = instruction->matching;
            pc = program.items[opening].matching + 1;
            continue;
        }
        if (strcmp(operation, "loop") == 0) {
            loops[loop_count++] = pc;
            ++pc;
            continue;
        }
        if (strcmp(operation, "break") == 0) {
            if (loop_count == 0) {
                snprintf(error, error_size, "Zeile %zu: break außerhalb einer aktiven Schleife.",
                         instruction->line);
                last_status = 2;
                break;
            }
            size_t opening = loops[--loop_count];
            pc = program.items[opening].matching + 1;
            continue;
        }
        if (strcmp(operation, "end") == 0) {
            size_t opening = instruction->matching;
            if (vx_is(&program.items[opening], "loop")) {
                if (loop_count == 0 || loops[loop_count - 1] != opening) {
                    snprintf(error, error_size, "Zeile %zu: Schleifenzustand inkonsistent.",
                             instruction->line);
                    last_status = 2;
                    break;
                }
                pc = opening + 1;
            } else {
                ++pc;
            }
            continue;
        }
        if (strcmp(operation, "return") == 0) {
            if (instruction->tokens.count == 2) {
                last_status = (int)strtol(instruction->tokens.items[1], NULL, 10);
            } else {
                last_status = 0;
            }
            break;
        }
        if (strcmp(operation, "call") == 0) {
            size_t count = instruction->tokens.count - 2;
            char **arguments = calloc(count + 1, sizeof(*arguments));
            if (arguments == NULL) {
                snprintf(error, error_size, "Zeile %zu: nicht genug Speicher.", instruction->line);
                last_status = 1;
                break;
            }
            int expanded_ok = 1;
            for (size_t index = 0; index < count; ++index) {
                arguments[index] = vx_expand(
                    instruction->tokens.items[index + 2], variables,
                    instruction->line, error, error_size
                );
                if (arguments[index] == NULL) {
                    expanded_ok = 0;
                    break;
                }
            }
            if (!expanded_ok) {
                for (size_t index = 0; index < count; ++index) {
                    free(arguments[index]);
                }
                free(arguments);
                last_status = 2;
                break;
            }
            if (error_size > 0) {
                error[0] = '\0';
            }
            if (handler == NULL) {
                snprintf(error, error_size, "Keine Aktionsimplementierung für '%s'.",
                         instruction->tokens.items[1]);
                last_status = 2;
            } else {
                last_status = handler(
                    context, instruction->tokens.items[1], count,
                    (const char *const *)arguments, variables, error, error_size
                );
            }
            for (size_t index = 0; index < count; ++index) {
                free(arguments[index]);
            }
            free(arguments);
            if (last_status != 0) {
                vx_variable_set(variables, "last_error",
                                error[0] == '\0' ? "Aktion fehlgeschlagen." : error);
            }
            ++pc;
            continue;
        }
        snprintf(error, error_size, "Zeile %zu: unerwarteter DSL-Zustand.", instruction->line);
        last_status = 2;
        break;
    }
    free(loops);
    vx_variables_destroy(variables);
    vx_program_destroy(&program);
    return last_status;
}

int vx_script_check_file(const char *path, char *error, size_t error_size)
{
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        snprintf(error, error_size, "%s: Datei konnte nicht geöffnet werden.", path);
        return 0;
    }
    char *source = malloc(VX_MAX_SOURCE + 1);
    if (source == NULL) {
        fclose(file);
        snprintf(error, error_size, "Nicht genug Speicher zum Lesen des DSL-Moduls.");
        return 0;
    }
    size_t length = fread(source, 1, VX_MAX_SOURCE + 1, file);
    int read_error = ferror(file);
    fclose(file);
    if (read_error || length > VX_MAX_SOURCE) {
        free(source);
        snprintf(error, error_size, "%s: DSL-Datei ist zu groß oder nicht lesbar.", path);
        return 0;
    }
    source[length] = '\0';
    VxProgram program = {0};
    int valid = vx_parse(source, &program, error, error_size);
    vx_program_destroy(&program);
    free(source);
    return valid;
}
