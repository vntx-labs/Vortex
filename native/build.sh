#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OS=$(uname -s)

case "$OS" in
    Linux)
        make -C "$ROOT" clean all
        ;;
    Darwin)
        CC=${CC:-clang}
        CFLAGS=${CFLAGS:--O2}
        "$CC" -std=c11 -Wall -Wextra -Werror -pedantic -I"$ROOT/include" \
            $CFLAGS -c "$ROOT/main.c" -o "$ROOT/main.o"
        "$CC" -std=c11 -Wall -Wextra -Werror -pedantic -I"$ROOT/include" \
            $CFLAGS -c "$ROOT/catalog.c" -o "$ROOT/catalog.o"
        "$CC" -std=c11 -Wall -Wextra -Werror -pedantic -I"$ROOT/include" \
            $CFLAGS -c "$ROOT/language.c" -o "$ROOT/language.o"
        "$CC" -std=c11 -Wall -Wextra -Werror -pedantic -I"$ROOT/include" \
            $CFLAGS -c "$ROOT/actions.c" -o "$ROOT/actions.o"
        "$CC" -std=c11 -Wall -Wextra -Werror -pedantic -I"$ROOT/include" \
            $CFLAGS -c "$ROOT/system_actions.c" -o "$ROOT/system_actions.o"
        "$CC" -std=c11 -Wall -Wextra -Werror -pedantic -I"$ROOT/include" \
            $CFLAGS -c "$ROOT/snippets.c" -o "$ROOT/snippets.o"
        "$CC" -std=c11 -Wall -Wextra -Werror -pedantic -I"$ROOT/include" \
            $CFLAGS -c "$ROOT/dialog.c" -o "$ROOT/dialog.o"
        "$CC" -Wall -Wextra -Werror -I"$ROOT/include" \
            $CFLAGS -c "$ROOT/platform/cocoa.m" -o "$ROOT/cocoa.o"
        "$CC" "$ROOT/main.o" "$ROOT/catalog.o" "$ROOT/language.o" \
            "$ROOT/actions.o" "$ROOT/system_actions.o" "$ROOT/snippets.o" \
            "$ROOT/dialog.o" "$ROOT/cocoa.o" \
            -framework AppKit -o "$ROOT/vortex-native"
        ;;
    *)
        printf 'Unsupported build host: %s\n' "$OS" >&2
        exit 2
        ;;
esac
