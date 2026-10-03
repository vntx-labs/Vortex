#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <windowsx.h>

#include "vortex_ui.h"

#include <stdlib.h>
#include <string.h>

struct VxWindow {
    HWND handle;
    HDC canvas;
    HBITMAP bitmap;
    HGDIOBJ previous_bitmap;
    int width;
    int height;
    VxEvent events[32];
    unsigned int event_head;
    unsigned int event_tail;
};

static const wchar_t *VX_WINDOW_CLASS = L"VortexNativeWindow";

static COLORREF vx_color(uint32_t color)
{
    return RGB((color >> 16) & 0xffu, (color >> 8) & 0xffu, color & 0xffu);
}

static void vx_push_event(VxWindow *window, VxEvent event)
{
    unsigned int next = (window->event_tail + 1u)
                      % (unsigned int)(sizeof(window->events) / sizeof(window->events[0]));
    if (next == window->event_head) {
        return;
    }
    window->events[window->event_tail] = event;
    window->event_tail = next;
}

static void vx_resize_surface(VxWindow *window, int width, int height)
{
    if (width < 1 || height < 1) {
        return;
    }
    if (window->previous_bitmap != NULL) {
        SelectObject(window->canvas, window->previous_bitmap);
        DeleteObject(window->bitmap);
    } else {
        window->canvas = CreateCompatibleDC(NULL);
    }
    HDC screen = GetDC(window->handle);
    window->bitmap = CreateCompatibleBitmap(screen, width, height);
    ReleaseDC(window->handle, screen);
    window->previous_bitmap = SelectObject(window->canvas, window->bitmap);
    window->width = width;
    window->height = height;
}

static LRESULT CALLBACK vx_window_proc(HWND handle, UINT message,
                                       WPARAM wparam, LPARAM lparam)
{
    VxWindow *window = (VxWindow *)GetWindowLongPtrW(handle, GWLP_USERDATA);
    if (message == WM_NCCREATE) {
        CREATESTRUCTW *creation = (CREATESTRUCTW *)lparam;
        window = (VxWindow *)creation->lpCreateParams;
        SetWindowLongPtrW(handle, GWLP_USERDATA, (LONG_PTR)window);
        window->handle = handle;
    }
    if (window == NULL) {
        return DefWindowProcW(handle, message, wparam, lparam);
    }
    VxEvent event = {0};
    switch (message) {
    case WM_PAINT: {
        PAINTSTRUCT paint;
        HDC screen = BeginPaint(handle, &paint);
        if (window->canvas != NULL) {
            BitBlt(screen, 0, 0, window->width, window->height,
                   window->canvas, 0, 0, SRCCOPY);
        }
        EndPaint(handle, &paint);
        event.type = VX_EVENT_EXPOSE;
        vx_push_event(window, event);
        return 0;
    }
    case WM_SIZE:
        vx_resize_surface(window, LOWORD(lparam), HIWORD(lparam));
        event.type = VX_EVENT_RESIZE;
        event.width = window->width;
        event.height = window->height;
        vx_push_event(window, event);
        return 0;
    case WM_LBUTTONDOWN:
    case WM_RBUTTONDOWN:
        event.type = VX_EVENT_POINTER_DOWN;
        event.x = GET_X_LPARAM(lparam);
        event.y = GET_Y_LPARAM(lparam);
        event.key = message == WM_LBUTTONDOWN ? 1u : 3u;
        vx_push_event(window, event);
        return 0;
    case WM_LBUTTONUP:
    case WM_RBUTTONUP:
        event.type = VX_EVENT_POINTER_UP;
        event.x = GET_X_LPARAM(lparam);
        event.y = GET_Y_LPARAM(lparam);
        event.key = message == WM_LBUTTONUP ? 1u : 3u;
        vx_push_event(window, event);
        return 0;
    case WM_KEYDOWN:
        event.type = VX_EVENT_KEY_DOWN;
        event.key = wparam == VK_ESCAPE ? VX_KEY_ESCAPE
            : wparam == VK_RETURN ? VX_KEY_RETURN
            : wparam == VK_BACK ? VX_KEY_BACKSPACE
            : (unsigned int)wparam;
        vx_push_event(window, event);
        return 0;
    case WM_CHAR: {
        wchar_t wide[2] = {(wchar_t)wparam, L'\0'};
        event.type = VX_EVENT_TEXT;
        int bytes = WideCharToMultiByte(CP_UTF8, 0, wide, 1, event.text,
                                        (int)sizeof(event.text) - 1, NULL, NULL);
        if (bytes > 0) {
            event.text[bytes] = '\0';
            vx_push_event(window, event);
        }
        return 0;
    }
    case WM_CLOSE:
        event.type = VX_EVENT_CLOSE;
        vx_push_event(window, event);
        return 0;
    case WM_ERASEBKGND:
        return 1;
    default:
        return DefWindowProcW(handle, message, wparam, lparam);
    }
}

VxWindow *vx_window_create(const char *title, int width, int height)
{
    static int class_registered;
    HINSTANCE instance = GetModuleHandleW(NULL);
    if (!class_registered) {
        WNDCLASSW definition = {0};
        definition.lpfnWndProc = vx_window_proc;
        definition.hInstance = instance;
        definition.hCursor = LoadCursorW(NULL, IDC_ARROW);
        definition.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
        definition.lpszClassName = VX_WINDOW_CLASS;
        if (RegisterClassW(&definition) == 0 && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
            return NULL;
        }
        class_registered = 1;
    }
    VxWindow *window = calloc(1, sizeof(*window));
    if (window == NULL) {
        return NULL;
    }
    int title_length = MultiByteToWideChar(CP_UTF8, 0, title, -1, NULL, 0);
    wchar_t *wide_title = calloc((size_t)title_length, sizeof(*wide_title));
    if (wide_title == NULL
        || MultiByteToWideChar(CP_UTF8, 0, title, -1, wide_title, title_length) == 0) {
        free(wide_title);
        free(window);
        return NULL;
    }
    RECT bounds = {0, 0, width, height};
    AdjustWindowRect(&bounds, WS_OVERLAPPEDWINDOW, FALSE);
    HWND handle = CreateWindowExW(
        0, VX_WINDOW_CLASS, wide_title, WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, bounds.right - bounds.left,
        bounds.bottom - bounds.top, NULL, NULL, instance, window
    );
    free(wide_title);
    if (handle == NULL) {
        free(window);
        return NULL;
    }
    vx_resize_surface(window, width, height);
    ShowWindow(handle, SW_SHOW);
    UpdateWindow(handle);
    return window;
}

void vx_window_destroy(VxWindow *window)
{
    if (window == NULL) {
        return;
    }
    if (window->canvas != NULL) {
        SelectObject(window->canvas, window->previous_bitmap);
        DeleteObject(window->bitmap);
        DeleteDC(window->canvas);
    }
    if (window->handle != NULL) {
        DestroyWindow(window->handle);
    }
    free(window);
}

int vx_window_next_event(VxWindow *window, VxEvent *event, int wait)
{
    while (window->event_head == window->event_tail) {
        MSG message;
        BOOL found = wait ? GetMessageW(&message, NULL, 0, 0)
                          : PeekMessageW(&message, NULL, 0, 0, PM_REMOVE);
        if (!found) {
            return 0;
        }
        TranslateMessage(&message);
        DispatchMessageW(&message);
        if (!wait && window->event_head == window->event_tail) {
            return 0;
        }
    }
    *event = window->events[window->event_head];
    window->event_head = (window->event_head + 1u)
                       % (unsigned int)(sizeof(window->events) / sizeof(window->events[0]));
    return 1;
}

void vx_begin_frame(VxWindow *window, uint32_t background)
{
    RECT area = {0, 0, window->width, window->height};
    HBRUSH brush = CreateSolidBrush(vx_color(background));
    FillRect(window->canvas, &area, brush);
    DeleteObject(brush);
}

void vx_fill_rect(VxWindow *window, int x, int y, int width, int height,
                  uint32_t color)
{
    RECT area = {x, y, x + width, y + height};
    HBRUSH brush = CreateSolidBrush(vx_color(color));
    FillRect(window->canvas, &area, brush);
    DeleteObject(brush);
}

void vx_draw_rect(VxWindow *window, int x, int y, int width, int height,
                  uint32_t color)
{
    HPEN pen = CreatePen(PS_SOLID, 1, vx_color(color));
    HGDIOBJ previous = SelectObject(window->canvas, pen);
    HGDIOBJ brush = SelectObject(window->canvas, GetStockObject(HOLLOW_BRUSH));
    Rectangle(window->canvas, x, y, x + width, y + height);
    SelectObject(window->canvas, brush);
    SelectObject(window->canvas, previous);
    DeleteObject(pen);
}

void vx_draw_line(VxWindow *window, int x1, int y1, int x2, int y2,
                  uint32_t color)
{
    HPEN pen = CreatePen(PS_SOLID, 1, vx_color(color));
    HGDIOBJ previous = SelectObject(window->canvas, pen);
    MoveToEx(window->canvas, x1, y1, NULL);
    LineTo(window->canvas, x2, y2);
    SelectObject(window->canvas, previous);
    DeleteObject(pen);
}

void vx_draw_text(VxWindow *window, int x, int baseline, uint32_t color,
                  const char *text)
{
    int length = MultiByteToWideChar(CP_UTF8, 0, text, -1, NULL, 0);
    wchar_t *wide = calloc((size_t)length, sizeof(*wide));
    if (wide == NULL
        || MultiByteToWideChar(CP_UTF8, 0, text, -1, wide, length) == 0) {
        free(wide);
        return;
    }
    SetBkMode(window->canvas, TRANSPARENT);
    SetTextColor(window->canvas, vx_color(color));
    TextOutW(window->canvas, x, baseline - 15, wide, length - 1);
    free(wide);
}

void vx_present(VxWindow *window)
{
    InvalidateRect(window->handle, NULL, FALSE);
    UpdateWindow(window->handle);
}
