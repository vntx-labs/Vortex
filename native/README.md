# Native C window backend

The first native backend uses X11 directly and draws through Xlib; it does not
use GTK, Tk, SDL, or another widget/graphics toolkit. The common drawing and
event API is in `include/vortex_ui.h`. `platform/win32.c` uses Win32 and GDI,
and `platform/cocoa.m` is the small Objective-C bridge required to open and
draw into native Cocoa windows. The application logic and module catalog
remain C.

On Linux with X11 development headers installed:

```sh
./native/build.sh
./native/vortex-native --list ./modules
```

macOS builds use the native Cocoa adapter through the repository's
`build.sh`. Windows builds use `build.bat` and a MinGW-w64 GCC toolchain on
`PATH`; these target adapters have not yet been compiled in this Linux
workspace.

The native module selector can be started with
`./native/vortex-native ./modules`. It reads `.vmod` files dynamically and
runs a selected `dsl` entry through the C interpreter. Implemented actions
currently include native message/confirmation/entry, selection-list,
editable-text, and text-preview dialogs; a native C file/directory browser;
canonical path, backup path-safety, temporary-file, preview,
log-tail, and cleanup operations; bounded argv-based process capture and port
validation/listening-process detection/termination; Linux clipboard writes;
and the permission-restricted Snippet-Hüter data store. Actions not yet
ported fail explicitly.

The POSIX filesystem/process and snippet-store implementations are currently
active on Linux and macOS. Windows builds retain explicit "not ported" action
results until equivalent Win32 implementations are added; this does not
change the existing launcher.

The existing application launcher is intentionally not switched yet. These
dialogs are basic building blocks, not feature-complete replacements for all
former GTK dialogs. The graphical module helpers (including Focus Code
Editor), optional-tool installation, several module-specific actions, and
the System-Check still need native implementations before Python can be
removed without losing features.

The Win32 and Cocoa adapters share the drawing/event API but are not build- or
runtime-validated in this Linux workspace. Wayland support is not included;
Linux currently targets X11 as agreed.
