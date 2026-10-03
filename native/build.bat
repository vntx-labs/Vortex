@echo off
setlocal
cd /d "%~dp0"

where gcc >nul 2>nul
if errorlevel 1 (
    echo Vortex native build requires a MinGW-w64 GCC toolchain on PATH. 1>&2
    exit /b 1
)

gcc -std=c11 -O2 -Wall -Wextra -Werror -Iinclude ^
    main.c catalog.c language.c actions.c system_actions.c snippets.c dialog.c platform\win32.c ^
    -lgdi32 -luser32 -o vortex-native.exe
if errorlevel 1 exit /b %errorlevel%
endlocal
