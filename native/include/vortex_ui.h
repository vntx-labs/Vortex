#ifndef VORTEX_UI_H
#define VORTEX_UI_H

#include <stdint.h>

#define VX_KEY_ESCAPE 0xff1bu
#define VX_KEY_RETURN 0xff0du
#define VX_KEY_BACKSPACE 0xff08u

typedef struct VxWindow VxWindow;

typedef enum {
    VX_EVENT_NONE,
    VX_EVENT_EXPOSE,
    VX_EVENT_RESIZE,
    VX_EVENT_POINTER_DOWN,
    VX_EVENT_POINTER_UP,
    VX_EVENT_KEY_DOWN,
    VX_EVENT_TEXT,
    VX_EVENT_CLOSE
} VxEventType;

typedef struct {
    VxEventType type;
    int x;
    int y;
    int width;
    int height;
    unsigned int key;
    char text[32];
} VxEvent;

VxWindow *vx_window_create(const char *title, int width, int height);
void vx_window_destroy(VxWindow *window);
int vx_window_next_event(VxWindow *window, VxEvent *event, int wait);

void vx_begin_frame(VxWindow *window, uint32_t background);
void vx_fill_rect(VxWindow *window, int x, int y, int width, int height,
                  uint32_t color);
void vx_draw_rect(VxWindow *window, int x, int y, int width, int height,
                  uint32_t color);
void vx_draw_line(VxWindow *window, int x1, int y1, int x2, int y2,
                  uint32_t color);
void vx_draw_text(VxWindow *window, int x, int baseline, uint32_t color,
                  const char *text);
void vx_present(VxWindow *window);

#endif
