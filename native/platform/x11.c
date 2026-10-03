#define _POSIX_C_SOURCE 200809L

#include "vortex_ui.h"

#include <locale.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <X11/keysym.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>

typedef struct {
    uint32_t rgb;
    unsigned long pixel;
} VxColor;

struct VxWindow {
    Display *display;
    int screen;
    Window handle;
    GC gc;
    XFontSet font;
    Atom wm_delete;
    int width;
    int height;
    VxColor colors[32];
    size_t color_count;
};

static unsigned long vx_pixel(VxWindow *window, uint32_t color)
{
    color &= 0xffffffu;
    for (size_t index = 0; index < window->color_count; ++index) {
        if (window->colors[index].rgb == color) {
            return window->colors[index].pixel;
        }
    }
    XColor value;
    Visual *visual = DefaultVisual(window->display, window->screen);
    Colormap map = DefaultColormap(window->display, window->screen);
    value.red = (unsigned short)(((color >> 16) & 0xffu) * 257u);
    value.green = (unsigned short)(((color >> 8) & 0xffu) * 257u);
    value.blue = (unsigned short)((color & 0xffu) * 257u);
    value.flags = DoRed | DoGreen | DoBlue;
    if (XAllocColor(window->display, map, &value) == 0) {
        const unsigned long red = (color >> 16) & 0xffu;
        const unsigned long green = (color >> 8) & 0xffu;
        const unsigned long blue = color & 0xffu;
        value.pixel = ((red << 16) & visual->red_mask)
                    | ((green << 8) & visual->green_mask)
                    | (blue & visual->blue_mask);
    }
    if (window->color_count < sizeof(window->colors) / sizeof(window->colors[0])) {
        window->colors[window->color_count++] = (VxColor){color, value.pixel};
    }
    return value.pixel;
}

VxWindow *vx_window_create(const char *title, int width, int height)
{
    VxWindow *window = calloc(1, sizeof(*window));
    if (window == NULL) {
        return NULL;
    }
    setlocale(LC_CTYPE, "");
    window->display = XOpenDisplay(NULL);
    if (window->display == NULL) {
        fprintf(stderr, "Vortex: kein X11-Display erreichbar.\n");
        free(window);
        return NULL;
    }
    window->screen = DefaultScreen(window->display);
    window->width = width;
    window->height = height;
    window->handle = XCreateSimpleWindow(
        window->display,
        RootWindow(window->display, window->screen),
        0, 0, (unsigned int)width, (unsigned int)height, 0,
        BlackPixel(window->display, window->screen),
        vx_pixel(window, 0xf3f6fbu)
    );
    XStoreName(window->display, window->handle, title);
    XSelectInput(window->display, window->handle,
                 ExposureMask | StructureNotifyMask | ButtonPressMask
                 | ButtonReleaseMask | KeyPressMask);
    window->wm_delete = XInternAtom(window->display, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(window->display, window->handle, &window->wm_delete, 1);
    window->gc = XCreateGC(window->display, window->handle, 0, NULL);
    window->font = XCreateFontSet(window->display, "-*-*-medium-r-normal--14-*-*-*-*-*-*-*",
                                  NULL, NULL, NULL);
    if (window->font == NULL) {
        window->font = XCreateFontSet(window->display, "fixed", NULL, NULL, NULL);
    }
    XMapWindow(window->display, window->handle);
    XFlush(window->display);
    return window;
}

void vx_window_destroy(VxWindow *window)
{
    if (window == NULL) {
        return;
    }
    if (window->font != NULL) {
        XFreeFontSet(window->display, window->font);
    }
    if (window->gc != NULL) {
        XFreeGC(window->display, window->gc);
    }
    if (window->handle != 0) {
        XDestroyWindow(window->display, window->handle);
    }
    if (window->display != NULL) {
        XCloseDisplay(window->display);
    }
    free(window);
}

int vx_window_next_event(VxWindow *window, VxEvent *event, int wait)
{
    XEvent native_event;
    memset(event, 0, sizeof(*event));
    if (!wait && XPending(window->display) == 0) {
        return 0;
    }
    XNextEvent(window->display, &native_event);
    switch (native_event.type) {
    case Expose:
        event->type = VX_EVENT_EXPOSE;
        break;
    case ConfigureNotify:
        event->type = VX_EVENT_RESIZE;
        event->width = native_event.xconfigure.width;
        event->height = native_event.xconfigure.height;
        window->width = event->width;
        window->height = event->height;
        break;
    case ButtonPress:
        event->type = VX_EVENT_POINTER_DOWN;
        event->x = native_event.xbutton.x;
        event->y = native_event.xbutton.y;
        event->key = native_event.xbutton.button;
        break;
    case ButtonRelease:
        event->type = VX_EVENT_POINTER_UP;
        event->x = native_event.xbutton.x;
        event->y = native_event.xbutton.y;
        event->key = native_event.xbutton.button;
        break;
    case KeyPress: {
        KeySym symbol = NoSymbol;
        int count = XLookupString(&native_event.xkey, event->text,
                                 (int)sizeof(event->text) - 1, &symbol, NULL);
        event->type = symbol == XK_Escape || symbol == XK_Return
            || symbol == XK_BackSpace || symbol == XK_Delete
            ? VX_EVENT_KEY_DOWN : count > 0 ? VX_EVENT_TEXT : VX_EVENT_KEY_DOWN;
        event->key = (unsigned int)symbol;
        if (count > 0) {
            event->text[count] = '\0';
        }
        break;
    }
    case ClientMessage:
        if ((Atom)native_event.xclient.data.l[0] == window->wm_delete) {
            event->type = VX_EVENT_CLOSE;
        }
        break;
    default:
        event->type = VX_EVENT_NONE;
        break;
    }
    return 1;
}

void vx_begin_frame(VxWindow *window, uint32_t background)
{
    XSetForeground(window->display, window->gc, vx_pixel(window, background));
    XFillRectangle(window->display, window->handle, window->gc, 0, 0,
                   (unsigned int)window->width, (unsigned int)window->height);
}

void vx_fill_rect(VxWindow *window, int x, int y, int width, int height,
                  uint32_t color)
{
    XSetForeground(window->display, window->gc, vx_pixel(window, color));
    XFillRectangle(window->display, window->handle, window->gc, x, y,
                   (unsigned int)width, (unsigned int)height);
}

void vx_draw_rect(VxWindow *window, int x, int y, int width, int height,
                  uint32_t color)
{
    XSetForeground(window->display, window->gc, vx_pixel(window, color));
    XDrawRectangle(window->display, window->handle, window->gc, x, y,
                   (unsigned int)width, (unsigned int)height);
}

void vx_draw_line(VxWindow *window, int x1, int y1, int x2, int y2,
                  uint32_t color)
{
    XSetForeground(window->display, window->gc, vx_pixel(window, color));
    XDrawLine(window->display, window->handle, window->gc, x1, y1, x2, y2);
}

void vx_draw_text(VxWindow *window, int x, int baseline, uint32_t color,
                  const char *text)
{
    XSetForeground(window->display, window->gc, vx_pixel(window, color));
    if (window->font != NULL) {
        Xutf8DrawString(window->display, window->handle, window->font,
                        window->gc, x, baseline, text, (int)strlen(text));
    } else {
        XDrawString(window->display, window->handle, window->gc,
                    x, baseline, text, (int)strlen(text));
    }
}

void vx_present(VxWindow *window)
{
    XFlush(window->display);
}
