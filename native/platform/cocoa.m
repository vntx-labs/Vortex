#import <AppKit/AppKit.h>

#include "vortex_ui.h"

#include <stdlib.h>
#include <string.h>

typedef enum {
    VX_OP_CLEAR,
    VX_OP_FILL,
    VX_OP_RECT,
    VX_OP_LINE,
    VX_OP_TEXT
} VxOperationType;

typedef struct {
    VxOperationType type;
    CGFloat x1;
    CGFloat y1;
    CGFloat x2;
    CGFloat y2;
    uint32_t color;
    char *text;
} VxOperation;

struct VxWindow {
    NSWindow *handle;
    id canvas;
    id delegate;
    int width;
    int height;
    VxOperation *operations;
    size_t operation_count;
    size_t operation_capacity;
    VxEvent events[32];
    unsigned int event_head;
    unsigned int event_tail;
};

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

static void vx_add_operation(VxWindow *window, VxOperation operation)
{
    if (window->operation_count == window->operation_capacity) {
        size_t capacity = window->operation_capacity == 0
            ? 64 : window->operation_capacity * 2;
        VxOperation *items = realloc(window->operations, capacity * sizeof(*items));
        if (items == NULL) {
            free(operation.text);
            return;
        }
        window->operations = items;
        window->operation_capacity = capacity;
    }
    window->operations[window->operation_count++] = operation;
}

static NSColor *vx_color(uint32_t color)
{
    return [NSColor colorWithCalibratedRed:(CGFloat)((color >> 16) & 0xffu) / 255.0
                                    green:(CGFloat)((color >> 8) & 0xffu) / 255.0
                                     blue:(CGFloat)(color & 0xffu) / 255.0
                                    alpha:1.0];
}

@interface VxCanvasView : NSView
@property(nonatomic, assign) VxWindow *owner;
@end

@implementation VxCanvasView
- (BOOL)isFlipped
{
    return YES;
}

- (BOOL)acceptsFirstResponder
{
    return YES;
}

- (void)setFrameSize:(NSSize)newSize
{
    [super setFrameSize:newSize];
    self.owner->width = (int)newSize.width;
    self.owner->height = (int)newSize.height;
    VxEvent result = {0};
    result.type = VX_EVENT_RESIZE;
    result.width = self.owner->width;
    result.height = self.owner->height;
    vx_push_event(self.owner, result);
}

- (void)drawRect:(NSRect)dirty
{
    (void)dirty;
    for (size_t index = 0; index < self.owner->operation_count; ++index) {
        VxOperation *operation = &self.owner->operations[index];
        NSColor *color = vx_color(operation->color);
        switch (operation->type) {
        case VX_OP_CLEAR:
        case VX_OP_FILL:
            [color setFill];
            NSRectFill(NSMakeRect(operation->x1, operation->y1,
                                  operation->x2, operation->y2));
            break;
        case VX_OP_RECT:
            [color setStroke];
            [[NSBezierPath bezierPathWithRect:
              NSMakeRect(operation->x1, operation->y1,
                         operation->x2, operation->y2)] stroke];
            break;
        case VX_OP_LINE: {
            [color setStroke];
            NSBezierPath *path = [NSBezierPath bezierPath];
            [path moveToPoint:NSMakePoint(operation->x1, operation->y1)];
            [path lineToPoint:NSMakePoint(operation->x2, operation->y2)];
            [path stroke];
            break;
        }
        case VX_OP_TEXT: {
            NSString *text = [[[NSString alloc] initWithUTF8String:operation->text]
                autorelease];
            NSDictionary *attributes = @{
                NSFontAttributeName: [NSFont systemFontOfSize:13],
                NSForegroundColorAttributeName: color
            };
            [text drawAtPoint:NSMakePoint(operation->x1, operation->y1)
               withAttributes:attributes];
            break;
        }
        }
    }
}

- (void)mouseDown:(NSEvent *)event
{
    NSPoint point = [self convertPoint:event.locationInWindow fromView:nil];
    VxEvent result = {0};
    result.type = VX_EVENT_POINTER_DOWN;
    result.x = (int)point.x;
    result.y = (int)point.y;
    result.key = 1;
    vx_push_event(self.owner, result);
}

- (void)mouseUp:(NSEvent *)event
{
    NSPoint point = [self convertPoint:event.locationInWindow fromView:nil];
    VxEvent result = {0};
    result.type = VX_EVENT_POINTER_UP;
    result.x = (int)point.x;
    result.y = (int)point.y;
    result.key = 1;
    vx_push_event(self.owner, result);
}

- (void)keyDown:(NSEvent *)event
{
    NSString *characters = event.charactersIgnoringModifiers;
    if (characters.length == 0) {
        return;
    }
    unichar character = [characters characterAtIndex:0];
    VxEvent result = {0};
    if (character < 32 || character == 0x7f) {
        result.type = VX_EVENT_KEY_DOWN;
        result.key = character == 0x1b ? VX_KEY_ESCAPE
            : character == '\r' ? VX_KEY_RETURN
            : character == 0x08 ? VX_KEY_BACKSPACE : character;
    } else {
        NSData *utf8 = [characters dataUsingEncoding:NSUTF8StringEncoding];
        NSUInteger length = MIN(utf8.length, sizeof(result.text) - 1);
        result.type = VX_EVENT_TEXT;
        memcpy(result.text, utf8.bytes, length);
        result.text[length] = '\0';
    }
    vx_push_event(self.owner, result);
}
@end

@interface VxWindowDelegate : NSObject <NSWindowDelegate>
@property(nonatomic, assign) VxWindow *owner;
@end

@implementation VxWindowDelegate
- (BOOL)windowShouldClose:(id)sender
{
    (void)sender;
    VxEvent result = {0};
    result.type = VX_EVENT_CLOSE;
    vx_push_event(self.owner, result);
    return NO;
}
@end

VxWindow *vx_window_create(const char *title, int width, int height)
{
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        VxWindow *window = calloc(1, sizeof(*window));
        if (window == NULL) {
            return NULL;
        }
        window->width = width;
        window->height = height;
        NSString *window_title = [[NSString alloc] initWithUTF8String:title];
        NSRect bounds = NSMakeRect(0, 0, width, height);
        window->handle = [[NSWindow alloc]
            initWithContentRect:bounds
                      styleMask:NSWindowStyleMaskTitled
                              | NSWindowStyleMaskClosable
                              | NSWindowStyleMaskResizable
                        backing:NSBackingStoreBuffered
                          defer:NO];
        if (window_title == nil || window->handle == nil) {
            [window_title release];
            [window->handle release];
            free(window);
            return NULL;
        }
        VxCanvasView *view = [[VxCanvasView alloc] initWithFrame:bounds];
        if (view == nil) {
            [window_title release];
            [window->handle release];
            free(window);
            return NULL;
        }
        view.owner = window;
        window->canvas = view;
        [window->handle setTitle:window_title];
        [window_title release];
        [window->handle setContentView:view];
        VxWindowDelegate *delegate = [[VxWindowDelegate alloc] init];
        delegate.owner = window;
        window->delegate = delegate;
        [window->handle setDelegate:delegate];
        [window->handle makeKeyAndOrderFront:nil];
        [NSApp activateIgnoringOtherApps:YES];
        return window;
    }
}

void vx_window_destroy(VxWindow *window)
{
    if (window == NULL) {
        return;
    }
    for (size_t index = 0; index < window->operation_count; ++index) {
        free(window->operations[index].text);
    }
    free(window->operations);
    [window->handle close];
    [window->delegate release];
    [window->canvas release];
    [window->handle release];
    free(window);
}

int vx_window_next_event(VxWindow *window, VxEvent *event, int wait)
{
    while (window->event_head == window->event_tail) {
        @autoreleasepool {
            NSDate *limit = wait ? [NSDate distantFuture] : [NSDate distantPast];
            NSEvent *native = [NSApp nextEventMatchingMask:NSEventMaskAny
                                                untilDate:limit
                                                   inMode:NSDefaultRunLoopMode
                                                  dequeue:YES];
            if (native == nil) {
                return 0;
            }
            [NSApp sendEvent:native];
            [NSApp updateWindows];
        }
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
    for (size_t index = 0; index < window->operation_count; ++index) {
        free(window->operations[index].text);
    }
    window->operation_count = 0;
    vx_add_operation(window, (VxOperation){
        .type = VX_OP_CLEAR, .x1 = 0, .y1 = 0,
        .x2 = window->width, .y2 = window->height, .color = background
    });
}

void vx_fill_rect(VxWindow *window, int x, int y, int width, int height,
                  uint32_t color)
{
    vx_add_operation(window, (VxOperation){
        .type = VX_OP_FILL, .x1 = x, .y1 = y, .x2 = width, .y2 = height,
        .color = color
    });
}

void vx_draw_rect(VxWindow *window, int x, int y, int width, int height,
                  uint32_t color)
{
    vx_add_operation(window, (VxOperation){
        .type = VX_OP_RECT, .x1 = x, .y1 = y, .x2 = width, .y2 = height,
        .color = color
    });
}

void vx_draw_line(VxWindow *window, int x1, int y1, int x2, int y2,
                  uint32_t color)
{
    vx_add_operation(window, (VxOperation){
        .type = VX_OP_LINE, .x1 = x1, .y1 = y1, .x2 = x2, .y2 = y2,
        .color = color
    });
}

void vx_draw_text(VxWindow *window, int x, int baseline, uint32_t color,
                  const char *text)
{
    vx_add_operation(window, (VxOperation){
        .type = VX_OP_TEXT, .x1 = x, .y1 = baseline - 14,
        .color = color, .text = strdup(text)
    });
}

void vx_present(VxWindow *window)
{
    [(NSView *)window->canvas setNeedsDisplay:YES];
    [window->handle displayIfNeeded];
}
