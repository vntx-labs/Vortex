#define _POSIX_C_SOURCE 200809L

#include "vortex_system_actions.h"

#ifdef _WIN32

#include <stdio.h>
#include <string.h>

int vx_system_action(const char *action, size_t count,
                     const char *const *arguments, VxVariables *variables,
                     int *handled, char *error, size_t error_size)
{
    (void)count;
    (void)arguments;
    (void)variables;
    *handled = 1;
    snprintf(error, error_size,
             "Native Aktion '%s' ist unter Windows noch nicht portiert.", action);
    return 2;
}

#else

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

enum {
    VX_ACTION_OUTPUT_LIMIT = 2 * 1024 * 1024,
    VX_ACTION_ERROR_LIMIT = 256
};

static int vx_action_arguments(size_t actual, size_t expected,
                               const char *action, char *error,
                               size_t error_size)
{
    if (actual == expected) {
        return 1;
    }
    snprintf(error, error_size, "%s erwartet %zu Argument(e), erhalten: %zu.",
             action, expected, actual);
    return 0;
}

static const char *vx_variable_path(const VxVariables *variables,
                                    const char *name)
{
    return vx_variable_get(variables, name);
}

static int vx_action_realpath(size_t count, const char *const *arguments,
                             VxVariables *variables, char *error,
                             size_t error_size)
{
    if (!vx_action_arguments(count, 1, "fs.realpath", error, error_size)) {
        return 2;
    }
    const char *path = vx_variable_path(variables, arguments[0]);
    if (path == NULL) {
        snprintf(error, error_size, "Unbekannte Pfadvariable '%s'.", arguments[0]);
        return 2;
    }
    char *resolved = realpath(path, NULL);
    if (resolved == NULL) {
        snprintf(error, error_size, "Pfad konnte nicht aufgelöst werden: %s",
                 strerror(errno));
        return 1;
    }
    int result = vx_variable_set(variables, arguments[0], resolved);
    free(resolved);
    if (!result) {
        snprintf(error, error_size, "Aufgelöster Pfad konnte nicht gespeichert werden.");
        return 1;
    }
    return 0;
}

static int vx_path_contains(const char *parent, const char *child)
{
    size_t parent_length = strlen(parent);
    return strncmp(parent, child, parent_length) == 0
        && (child[parent_length] == '\0' || parent[parent_length - 1] == '/'
            || child[parent_length] == '/');
}

static int vx_action_assert_disjoint(size_t count,
                                     const char *const *arguments,
                                     VxVariables *variables, char *error,
                                     size_t error_size)
{
    if (!vx_action_arguments(count, 2, "fs.assert_disjoint", error, error_size)) {
        return 2;
    }
    const char *first = vx_variable_path(variables, arguments[0]);
    const char *second = vx_variable_path(variables, arguments[1]);
    if (first == NULL || second == NULL) {
        snprintf(error, error_size, "Unbekannte Pfadvariable für fs.assert_disjoint.");
        return 2;
    }
    char *resolved_first = realpath(first, NULL);
    char *resolved_second = realpath(second, NULL);
    if (resolved_first == NULL || resolved_second == NULL) {
        snprintf(error, error_size, "Ordner konnten nicht geprüft werden: %s",
                 strerror(errno));
        free(resolved_first);
        free(resolved_second);
        return 1;
    }
    struct stat first_info;
    struct stat second_info;
    if (stat(resolved_first, &first_info) != 0
        || stat(resolved_second, &second_info) != 0
        || !S_ISDIR(first_info.st_mode) || !S_ISDIR(second_info.st_mode)) {
        snprintf(error, error_size, "Quell- und Zielpfad müssen vorhandene Ordner sein.");
        free(resolved_first);
        free(resolved_second);
        return 1;
    }
    int overlaps = vx_path_contains(resolved_first, resolved_second)
        || vx_path_contains(resolved_second, resolved_first);
    free(resolved_first);
    free(resolved_second);
    if (overlaps) {
        snprintf(error, error_size,
                 "Quell- und Zielordner dürfen weder identisch sein noch ineinander liegen.");
        return 1;
    }
    return 0;
}

static int vx_action_temp(size_t count, const char *const *arguments,
                          VxVariables *variables, char *error,
                          size_t error_size)
{
    if (!vx_action_arguments(count, 2, "fs.temp", error, error_size)) {
        return 2;
    }
    const char *prefix = arguments[1];
    if (strchr(prefix, '/') != NULL || prefix[0] == '\0') {
        snprintf(error, error_size, "Ungültiges Präfix für temporäre Datei.");
        return 2;
    }
    const char *temporary_directory = getenv("TMPDIR");
    if (temporary_directory == NULL || temporary_directory[0] == '\0') {
        temporary_directory = "/tmp";
    }
    size_t needed = strlen(temporary_directory) + strlen(prefix) + 10;
    char *template = malloc(needed);
    if (template == NULL) {
        snprintf(error, error_size, "Nicht genug Speicher für temporäre Datei.");
        return 1;
    }
    snprintf(template, needed, "%s/%sXXXXXX", temporary_directory, prefix);
    int descriptor = mkstemp(template);
    if (descriptor < 0) {
        snprintf(error, error_size, "Temporäre Datei konnte nicht erstellt werden: %s",
                 strerror(errno));
        free(template);
        return 1;
    }
    int result = close(descriptor);
    if (result != 0 || !vx_variable_set(variables, arguments[0], template)) {
        int saved_errno = errno;
        (void)unlink(template);
        snprintf(error, error_size, "Temporärer Pfad konnte nicht gespeichert werden: %s",
                 result != 0 ? strerror(saved_errno) : "Speicherfehler");
        free(template);
        return 1;
    }
    free(template);
    return 0;
}

static int vx_read_bounded(const char *path, char **contents, size_t limit,
                           char *error, size_t error_size)
{
    int descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) {
        snprintf(error, error_size, "%s: %s", path, strerror(errno));
        return 0;
    }
    struct stat information;
    if (fstat(descriptor, &information) != 0 || !S_ISREG(information.st_mode)) {
        snprintf(error, error_size, "%s ist keine reguläre Datei.", path);
        close(descriptor);
        return 0;
    }
    if (information.st_size < 0 || (uintmax_t)information.st_size > limit) {
        snprintf(error, error_size, "%s überschreitet das Leselimit.", path);
        close(descriptor);
        return 0;
    }
    size_t capacity = (size_t)information.st_size + 1;
    if (capacity == 0) {
        snprintf(error, error_size, "%s ist zu groß.", path);
        close(descriptor);
        return 0;
    }
    char *buffer = malloc(capacity);
    if (buffer == NULL) {
        snprintf(error, error_size, "Nicht genug Speicher zum Lesen von %s.", path);
        close(descriptor);
        return 0;
    }
    size_t used = 0;
    while (used < capacity - 1) {
        ssize_t bytes = read(descriptor, buffer + used, capacity - 1 - used);
        if (bytes < 0 && errno == EINTR) {
            continue;
        }
        if (bytes < 0) {
            snprintf(error, error_size, "%s konnte nicht gelesen werden: %s",
                     path, strerror(errno));
            free(buffer);
            close(descriptor);
            return 0;
        }
        if (bytes == 0) {
            break;
        }
        used += (size_t)bytes;
    }
    if (close(descriptor) != 0) {
        snprintf(error, error_size, "%s konnte nicht geschlossen werden: %s",
                 path, strerror(errno));
        free(buffer);
        return 0;
    }
    buffer[used] = '\0';
    *contents = buffer;
    return 1;
}

static int vx_action_compose_preview(size_t count,
                                     const char *const *arguments,
                                     VxVariables *variables, char *error,
                                     size_t error_size)
{
    if (!vx_action_arguments(count, 4, "fs.compose_preview", error, error_size)) {
        return 2;
    }
    const char *output_path = vx_variable_path(variables, arguments[0]);
    const char *log_path = vx_variable_path(variables, arguments[1]);
    const char *source = vx_variable_path(variables, arguments[2]);
    const char *destination = vx_variable_path(variables, arguments[3]);
    if (output_path == NULL || log_path == NULL || source == NULL || destination == NULL) {
        snprintf(error, error_size, "Unbekannte Variable für Backup-Vorschau.");
        return 2;
    }
    char *log = NULL;
    if (!vx_read_bounded(log_path, &log, 2 * 1024 * 1024, error, error_size)) {
        return 1;
    }
    int descriptor = open(output_path, O_WRONLY | O_TRUNC | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) {
        snprintf(error, error_size, "Backup-Vorschau konnte nicht geschrieben werden: %s",
                 strerror(errno));
        free(log);
        return 1;
    }
    FILE *output = fdopen(descriptor, "w");
    if (output == NULL) {
        snprintf(error, error_size, "Backup-Vorschau konnte nicht geöffnet werden: %s",
                 strerror(errno));
        close(descriptor);
        free(log);
        return 1;
    }
    size_t log_length = strlen(log);
    size_t lines = 0;
    size_t copied = 0;
    while (copied < log_length && lines < 180) {
        if (log[copied++] == '\n') {
            ++lines;
        }
    }
    int write_ok = fprintf(output, "Quelle: %s\nZiel: %s\n\n", source, destination) >= 0
        && fwrite(log, 1, copied, output) == copied
        && fprintf(output, "\nVorschau gekürzt, falls weitere Einträge folgen.\n") >= 0;
    if (fclose(output) != 0) {
        write_ok = 0;
    }
    free(log);
    if (!write_ok) {
        snprintf(error, error_size, "Backup-Vorschau konnte nicht vollständig geschrieben werden.");
        return 1;
    }
    return 0;
}

static int vx_action_tail(size_t count, const char *const *arguments,
                          VxVariables *variables, char *error,
                          size_t error_size)
{
    if (!vx_action_arguments(count, 3, "fs.tail", error, error_size)) {
        return 2;
    }
    const char *path = vx_variable_path(variables, arguments[0]);
    if (path == NULL) {
        snprintf(error, error_size, "Unbekannte Protokollvariable '%s'.", arguments[0]);
        return 2;
    }
    char *end = NULL;
    long requested = strtol(arguments[2], &end, 10);
    if (end == arguments[2] || *end != '\0') {
        snprintf(error, error_size, "fs.tail erwartet eine ganzzahlige Zeilenanzahl.");
        return 2;
    }
    size_t wanted = requested < 1 ? 1 : requested > 100 ? 100 : (size_t)requested;
    char *contents = NULL;
    if (!vx_read_bounded(path, &contents, 2 * 1024 * 1024, error, error_size)) {
        return 1;
    }
    size_t length = strlen(contents);
    size_t start = length;
    size_t lines = 0;
    while (start > 0 && lines < wanted) {
        --start;
        if (contents[start] == '\n' && start + 1 < length) {
            ++lines;
            if (lines == wanted) {
                ++start;
                break;
            }
        }
    }
    int ok = vx_variable_set(variables, arguments[1], contents + start);
    free(contents);
    if (!ok) {
        snprintf(error, error_size, "Protokollauszug konnte nicht gespeichert werden.");
        return 1;
    }
    return 0;
}

static int vx_action_remove(size_t count, const char *const *arguments,
                            VxVariables *variables, char *error,
                            size_t error_size)
{
    if (count == 0) {
        snprintf(error, error_size, "fs.remove erwartet mindestens eine Pfadvariable.");
        return 2;
    }
    for (size_t index = 0; index < count; ++index) {
        const char *path = vx_variable_path(variables, arguments[index]);
        if (path == NULL) {
            snprintf(error, error_size, "Unbekannte Pfadvariable '%s'.", arguments[index]);
            return 2;
        }
        if (unlink(path) != 0 && errno != ENOENT) {
            snprintf(error, error_size, "Temporäre Datei konnte nicht entfernt werden: %s",
                     strerror(errno));
            return 1;
        }
    }
    return 0;
}

static int vx_parse_timeout(const char *text, double *seconds)
{
    char *end = NULL;
    errno = 0;
    double value = strtod(text, &end);
    if (errno != 0 || end == text || *end != '\0' || value <= 0.0
        || value > 3600.0) {
        return 0;
    }
    *seconds = value;
    return 1;
}

static int vx_action_capture(size_t count, const char *const *arguments,
                             VxVariables *variables, char *error,
                             size_t error_size)
{
    if (count < 4) {
        snprintf(error, error_size,
                 "process.capture erwartet Ausgabevariable, Zeitlimit, Programm und Argumente.");
        return 2;
    }
    const char *output_path = vx_variable_path(variables, arguments[0]);
    if (output_path == NULL) {
        snprintf(error, error_size, "Unbekannte Ausgabevariable '%s'.", arguments[0]);
        return 2;
    }
    double timeout;
    if (!vx_parse_timeout(arguments[1], &timeout)) {
        snprintf(error, error_size, "Ungültiges Zeitlimit für process.capture.");
        return 2;
    }
    int descriptor = open(output_path,
                          O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW,
                          0600);
    if (descriptor < 0) {
        snprintf(error, error_size, "Prozessausgabe konnte nicht angelegt werden: %s",
                 strerror(errno));
        return 1;
    }
    char **command = calloc(count - 1, sizeof(*command));
    if (command == NULL) {
        snprintf(error, error_size, "Nicht genug Speicher zum Starten des Prozesses.");
        close(descriptor);
        return 1;
    }
    for (size_t index = 2; index < count; ++index) {
        command[index - 2] = (char *)arguments[index];
    }
    struct stat file_info;
    if (fstat(descriptor, &file_info) != 0 || !S_ISREG(file_info.st_mode)) {
        snprintf(error, error_size, "Prozessausgabe ist keine reguläre Datei.");
        close(descriptor);
        free(command);
        return 1;
    }
    pid_t child = fork();
    if (child < 0) {
        snprintf(error, error_size, "Prozess konnte nicht gestartet werden: %s",
                 strerror(errno));
        close(descriptor);
        free(command);
        return 1;
    }
    if (child == 0) {
        if (dup2(descriptor, STDOUT_FILENO) < 0
            || dup2(descriptor, STDERR_FILENO) < 0) {
            _exit(126);
        }
        close(descriptor);
        (void)setpgid(0, 0);
        execvp(command[0], command);
        dprintf(STDERR_FILENO, "%s konnte nicht gestartet werden: %s\n",
                command[0], strerror(errno));
        _exit(errno == ENOENT ? 127 : 126);
    }
    (void)setpgid(child, child);
    free(command);
    close(descriptor);
    struct timespec start;
    if (clock_gettime(CLOCK_MONOTONIC, &start) != 0) {
        kill(child, SIGTERM);
        (void)waitpid(child, NULL, 0);
        snprintf(error, error_size, "Monotone Uhr ist nicht verfügbar: %s",
                 strerror(errno));
        return 1;
    }
    int child_status = 0;
    for (;;) {
        pid_t waited = waitpid(child, &child_status, WNOHANG);
        if (waited == child) {
            break;
        }
        if (waited < 0 && errno != EINTR) {
            snprintf(error, error_size, "Prozessstatus konnte nicht gelesen werden: %s",
                     strerror(errno));
            return 1;
        }
        struct timespec now;
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
            kill(child, SIGTERM);
            (void)waitpid(child, NULL, 0);
            snprintf(error, error_size, "Monotone Uhr ist nicht verfügbar: %s",
                     strerror(errno));
            return 1;
        }
        double elapsed = (double)(now.tv_sec - start.tv_sec)
            + (double)(now.tv_nsec - start.tv_nsec) / 1000000000.0;
        if (elapsed >= timeout) {
            kill(-child, SIGTERM);
            struct timespec grace = {.tv_sec = 0, .tv_nsec = 250000000};
            (void)nanosleep(&grace, NULL);
            if (waitpid(child, &child_status, WNOHANG) == 0) {
                kill(-child, SIGKILL);
            }
            while (waitpid(child, &child_status, 0) < 0 && errno == EINTR) {
            }
            snprintf(error, error_size,
                     "Prozess wurde nach %.0f Sekunden Zeitlimit beendet.", timeout);
            return 124;
        }
        struct timespec delay = {.tv_sec = 0, .tv_nsec = 20000000};
        (void)nanosleep(&delay, NULL);
    }
    if (WIFEXITED(child_status)) {
        return WEXITSTATUS(child_status);
    }
    if (WIFSIGNALED(child_status)) {
        snprintf(error, error_size, "Prozess wurde durch Signal %d beendet.",
                 WTERMSIG(child_status));
        return 128 + WTERMSIG(child_status);
    }
    snprintf(error, error_size, "Prozess endete in einem unbekannten Zustand.");
    return 1;
}

static int vx_action_validate_port(size_t count,
                                  const char *const *arguments, char *error,
                                  size_t error_size)
{
    if (!vx_action_arguments(count, 1, "process.validate_port", error, error_size)) {
        return 2;
    }
    char *end = NULL;
    long port = strtol(arguments[0], &end, 10);
    if (end == arguments[0] || *end != '\0' || port < 1 || port > 65535) {
        snprintf(error, error_size, "Bitte einen gültigen Port zwischen 1 und 65535 eingeben.");
        return 1;
    }
    return 0;
}

static int vx_executable_available(const char *name)
{
    const char *path = getenv("PATH");
    if (path == NULL) {
        return 0;
    }
    const char *cursor = path;
    while (*cursor != '\0') {
        const char *end = strchr(cursor, ':');
        size_t length = end == NULL ? strlen(cursor) : (size_t)(end - cursor);
        size_t needed = length + strlen(name) + 2;
        char *candidate = malloc(needed);
        if (candidate == NULL) {
            return 0;
        }
        if (length == 0) {
            snprintf(candidate, needed, "./%s", name);
        } else {
            snprintf(candidate, needed, "%.*s/%s", (int)length, cursor, name);
        }
        int available = access(candidate, X_OK) == 0;
        free(candidate);
        if (available) {
            return 1;
        }
        if (end == NULL) {
            break;
        }
        cursor = end + 1;
    }
    return 0;
}

static int vx_run_pids(const char *port, char *output, size_t output_size,
                       char *error, size_t error_size)
{
    const char *tool = vx_executable_available("lsof") ? "lsof"
        : vx_executable_available("fuser") ? "fuser" : NULL;
    if (tool == NULL) {
        snprintf(error, error_size, "Weder lsof noch fuser ist verfügbar.");
        return 1;
    }
    int pipe_fds[2];
    if (pipe(pipe_fds) != 0) {
        snprintf(error, error_size, "Port-Erkennung konnte nicht gestartet werden: %s",
                 strerror(errno));
        return 1;
    }
    pid_t child = fork();
    if (child < 0) {
        snprintf(error, error_size, "Port-Erkennung konnte nicht gestartet werden: %s",
                 strerror(errno));
        close(pipe_fds[0]);
        close(pipe_fds[1]);
        return 1;
    }
    if (child == 0) {
        close(pipe_fds[0]);
        int null_output = open("/dev/null", O_WRONLY | O_CLOEXEC);
        if (dup2(pipe_fds[1], STDOUT_FILENO) < 0 || null_output < 0
            || dup2(null_output, STDERR_FILENO) < 0) {
            _exit(126);
        }
        close(null_output);
        close(pipe_fds[1]);
        if (strcmp(tool, "lsof") == 0) {
            char selector[32];
            snprintf(selector, sizeof(selector), "-iTCP:%s", port);
            execlp(tool, tool, "-nP", "-t", selector, "-sTCP:LISTEN", (char *)NULL);
        } else {
            execlp(tool, tool, "-n", "tcp", port, (char *)NULL);
        }
        _exit(errno == ENOENT ? 127 : 126);
    }
    close(pipe_fds[1]);
    size_t used = 0;
    int overflow = 0;
    char buffer[512];
    for (;;) {
        ssize_t bytes = read(pipe_fds[0], buffer, sizeof(buffer));
        if (bytes < 0 && errno == EINTR) {
            continue;
        }
        if (bytes < 0) {
            snprintf(error, error_size, "Port-Erkennungsausgabe konnte nicht gelesen werden: %s",
                     strerror(errno));
            close(pipe_fds[0]);
            kill(child, SIGTERM);
            (void)waitpid(child, NULL, 0);
            return 1;
        }
        if (bytes == 0) {
            break;
        }
        size_t count = (size_t)bytes;
        if (count >= output_size - used) {
            overflow = 1;
            count = output_size - used - 1;
        }
        memcpy(output + used, buffer, count);
        used += count;
    }
    close(pipe_fds[0]);
    int status = 0;
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {
    }
    output[used] = '\0';
    if (overflow) {
        snprintf(error, error_size, "Port-Erkennung lieferte zu viele Ergebnisse.");
        return 1;
    }
    if (!WIFEXITED(status) || (WEXITSTATUS(status) != 0 && WEXITSTATUS(status) != 1)) {
        snprintf(error, error_size, "Port-Erkennung ist fehlgeschlagen.");
        return 1;
    }
    return 0;
}

static int vx_pid_is_listener(const char *port, const char *pid,
                              char *error, size_t error_size)
{
    char output[8192];
    int status = vx_run_pids(port, output, sizeof(output), error, error_size);
    if (status != 0) {
        return -1;
    }
    const char *cursor = output;
    size_t pid_length = strlen(pid);
    while (*cursor != '\0') {
        const char *line_end = strchr(cursor, '\n');
        size_t line_length = line_end == NULL ? strlen(cursor)
            : (size_t)(line_end - cursor);
        if (line_length == pid_length && memcmp(cursor, pid, pid_length) == 0) {
            return 1;
        }
        cursor = line_end == NULL ? cursor + line_length : line_end + 1;
    }
    return 0;
}

static int vx_parse_pid(const char *text, pid_t *pid)
{
    char *end = NULL;
    errno = 0;
    long value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value <= 0
        || value > INT_MAX) {
        return 0;
    }
    *pid = (pid_t)value;
    return 1;
}

static int vx_action_terminate_listener(size_t count,
                                        const char *const *arguments,
                                        VxVariables *variables, char *error,
                                        size_t error_size)
{
    if (!vx_action_arguments(count, 3, "process.terminate_listener",
                             error, error_size)) {
        return 2;
    }
    int status = vx_action_validate_port(1, &arguments[0], error, error_size);
    pid_t pid;
    if (status != 0) {
        return status;
    }
    if (!vx_parse_pid(arguments[1], &pid)) {
        snprintf(error, error_size, "Ungültige Prozess-ID für die Beendigung.");
        return 1;
    }
    if (strcmp(vx_variable_get(variables, arguments[2]) == NULL ? ""
                   : vx_variable_get(variables, arguments[2]), "yes") != 0) {
        snprintf(error, error_size, "SIGTERM wurde nicht ausdrücklich bestätigt.");
        return 1;
    }
    int listening = vx_pid_is_listener(arguments[0], arguments[1], error, error_size);
    if (listening < 0) {
        return 1;
    }
    if (!listening) {
        snprintf(error, error_size,
                 "PID %s blockiert Port %s nicht mehr. Bitte erneut prüfen.",
                 arguments[1], arguments[0]);
        return 1;
    }
    if (kill(pid, 0) != 0 || kill(pid, SIGTERM) != 0) {
        snprintf(error, error_size, "SIGTERM konnte nicht an PID %s gesendet werden: %s",
                 arguments[1], strerror(errno));
        return 1;
    }
    int still_running = 1;
    for (int attempt = 0; attempt < 20; ++attempt) {
        if (kill(pid, 0) != 0 && errno == ESRCH) {
            still_running = 0;
            break;
        }
        struct timespec delay = {.tv_sec = 0, .tv_nsec = 100000000};
        (void)nanosleep(&delay, NULL);
    }
    if (!vx_variable_set(variables, "listener_still_running",
                         still_running ? "yes" : "no")) {
        snprintf(error, error_size, "Prozessstatus konnte nicht gespeichert werden.");
        return 1;
    }
    return 0;
}

static int vx_action_kill_selected(size_t count,
                                   const char *const *arguments,
                                   VxVariables *variables, char *error,
                                   size_t error_size)
{
    if (!vx_action_arguments(count, 2, "process.kill_selected", error, error_size)) {
        return 2;
    }
    pid_t pid;
    if (!vx_parse_pid(arguments[0], &pid)) {
        snprintf(error, error_size, "Ungültige Prozess-ID für SIGKILL.");
        return 1;
    }
    const char *confirmation = vx_variable_get(variables, arguments[1]);
    if (confirmation == NULL || strcmp(confirmation, "yes") != 0) {
        snprintf(error, error_size, "SIGKILL wurde nicht ausdrücklich bestätigt.");
        return 1;
    }
    if (kill(pid, 0) != 0) {
        snprintf(error, error_size, "PID %s ist nicht mehr aktiv: %s",
                 arguments[0], strerror(errno));
        return 1;
    }
    if (kill(pid, SIGKILL) != 0) {
        snprintf(error, error_size, "SIGKILL konnte nicht an PID %s gesendet werden: %s",
                 arguments[0], strerror(errno));
        return 1;
    }
    return 0;
}

int vx_system_action(const char *action, size_t count,
                     const char *const *arguments, VxVariables *variables,
                     int *handled, char *error, size_t error_size)
{
    *handled = 1;
    if (strcmp(action, "fs.realpath") == 0) {
        return vx_action_realpath(count, arguments, variables, error, error_size);
    }
    if (strcmp(action, "fs.assert_disjoint") == 0) {
        return vx_action_assert_disjoint(count, arguments, variables, error, error_size);
    }
    if (strcmp(action, "fs.temp") == 0) {
        return vx_action_temp(count, arguments, variables, error, error_size);
    }
    if (strcmp(action, "fs.compose_preview") == 0) {
        return vx_action_compose_preview(count, arguments, variables, error, error_size);
    }
    if (strcmp(action, "fs.tail") == 0) {
        return vx_action_tail(count, arguments, variables, error, error_size);
    }
    if (strcmp(action, "fs.remove") == 0) {
        return vx_action_remove(count, arguments, variables, error, error_size);
    }
    if (strcmp(action, "process.capture") == 0) {
        return vx_action_capture(count, arguments, variables, error, error_size);
    }
    if (strcmp(action, "process.validate_port") == 0) {
        return vx_action_validate_port(count, arguments, error, error_size);
    }
    if (strcmp(action, "process.ensure_port_tool") == 0) {
        if (!vx_action_arguments(count, 0, action, error, error_size)) {
            return 2;
        }
        if (vx_executable_available("lsof") || vx_executable_available("fuser")) {
            return 0;
        }
        snprintf(error, error_size, "Weder lsof noch fuser ist verfügbar.");
        return 1;
    }
    if (strcmp(action, "process.listener_pids") == 0) {
        if (!vx_action_arguments(count, 2, action, error, error_size)) {
            return 2;
        }
        int status = vx_action_validate_port(1, &arguments[1], error, error_size);
        if (status != 0) {
            return status;
        }
        char output[8192];
        status = vx_run_pids(arguments[1], output, sizeof(output), error, error_size);
        if (status != 0) {
            return status;
        }
        char normalized[8192];
        size_t output_length = 0;
        for (char *cursor = output; *cursor != '\0';) {
            while (*cursor != '\0' && (*cursor < '0' || *cursor > '9')) {
                ++cursor;
            }
            if (*cursor == '\0') {
                break;
            }
            char *start = cursor;
            while (*cursor >= '0' && *cursor <= '9') {
                ++cursor;
            }
            size_t length = (size_t)(cursor - start);
            if (output_length + length + 2 >= sizeof(normalized)) {
                snprintf(error, error_size, "Zu viele Prozess-IDs für den DSL-Wert.");
                return 1;
            }
            memcpy(normalized + output_length, start, length);
            output_length += length;
            normalized[output_length++] = '\n';
        }
        normalized[output_length] = '\0';
        if (!vx_variable_set(variables, arguments[0], normalized)) {
            snprintf(error, error_size, "Prozess-IDs konnten nicht gespeichert werden.");
            return 1;
        }
        return 0;
    }
    if (strcmp(action, "process.terminate_listener") == 0) {
        return vx_action_terminate_listener(count, arguments, variables,
                                           error, error_size);
    }
    if (strcmp(action, "process.kill_selected") == 0) {
        return vx_action_kill_selected(count, arguments, variables,
                                       error, error_size);
    }
    *handled = 0;
    return 0;
}

#endif
