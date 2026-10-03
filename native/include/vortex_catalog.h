#ifndef VORTEX_CATALOG_H
#define VORTEX_CATALOG_H

#include <stddef.h>

typedef struct {
    char *module_id;
    char *name;
    char *description;
    char *icon;
    char *backend;
    char *entry;
    int order;
} VxModule;

typedef struct {
    VxModule *items;
    size_t count;
} VxCatalog;

int vx_catalog_load(const char *directory, VxCatalog *catalog,
                    char *error, size_t error_size);
void vx_catalog_destroy(VxCatalog *catalog);

#endif
