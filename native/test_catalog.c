#define _POSIX_C_SOURCE 200809L

#include "vortex_catalog.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int write_manifest(const char *directory, const char *entry)
{
    char path[1024];
    int length = snprintf(path, sizeof(path), "%s/test.vmod", directory);
    if (length < 0 || (size_t)length >= sizeof(path)) {
        return 0;
    }
    FILE *file = fopen(path, "w");
    if (file == NULL) {
        return 0;
    }
    int result = fprintf(file,
        "module test\nname \"Test\"\ndescription \"Test module\"\n"
        "icon \"test\"\nbackend dsl\nentry \"%s\"\norder 1\n",
        entry);
    return fclose(file) == 0 && result > 0;
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        return 2;
    }
    char error[512] = {0};
    VxCatalog catalog = {0};
    if (!vx_catalog_load(argv[1], &catalog, error, sizeof(error))) {
        fprintf(stderr, "Catalog load failed: %s\n", error);
        return 1;
    }
    if (catalog.count != 10) {
        fprintf(stderr, "Expected 10 module manifests, found %zu\n", catalog.count);
        vx_catalog_destroy(&catalog);
        return 1;
    }
    for (size_t index = 1; index < catalog.count; ++index) {
        if (catalog.items[index - 1].order > catalog.items[index].order) {
            fprintf(stderr, "Catalog order is not sorted\n");
            vx_catalog_destroy(&catalog);
            return 1;
        }
    }
    vx_catalog_destroy(&catalog);

    char temporary[] = "/tmp/vortex-catalog-test.XXXXXX";
    char *directory = mkdtemp(temporary);
    if (directory == NULL) {
        perror("mkdtemp");
        return 1;
    }
    char outside[] = "/tmp/vortex-catalog-outside.XXXXXX";
    int descriptor = mkstemp(outside);
    if (descriptor < 0) {
        perror("mkstemp");
        rmdir(directory);
        return 1;
    }
    close(descriptor);
    char link_path[1024];
    snprintf(link_path, sizeof(link_path), "%s/escape.vscript", directory);
    int ok = symlink(outside, link_path) == 0 && write_manifest(directory, "escape.vscript");
    VxCatalog rejected = {0};
    error[0] = '\0';
    if (!ok || vx_catalog_load(directory, &rejected, error, sizeof(error))) {
        fprintf(stderr, "Catalog accepted an entry symlink escaping its module folder\n");
        vx_catalog_destroy(&rejected);
        unlink(link_path);
        unlink(outside);
        rmdir(directory);
        return 1;
    }
    unlink(link_path);
    char manifest_path[1024];
    snprintf(manifest_path, sizeof(manifest_path), "%s/test.vmod", directory);
    unlink(manifest_path);
    unlink(outside);
    rmdir(directory);
    puts("C module catalog OK");
    return 0;
}
