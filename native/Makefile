CC ?= cc
CFLAGS ?= -O2
WARNINGS = -std=c11 -Wall -Wextra -Werror -pedantic
CPPFLAGS += -Iinclude
LDLIBS += -lX11

all: vortex-native

vortex-native: main.o catalog.o language.o actions.o system_actions.o snippets.o dialog.o platform/x11.o
	$(CC) $(CFLAGS) $(WARNINGS) -o $@ $^ $(LDLIBS)

main.o: main.c include/vortex_ui.h include/vortex_catalog.h include/vortex_language.h include/vortex_actions.h include/vortex_dialog.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

catalog.o: catalog.c include/vortex_catalog.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

language.o: language.c include/vortex_language.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

actions.o: actions.c include/vortex_actions.h include/vortex_dialog.h include/vortex_snippets.h include/vortex_system_actions.h include/vortex_ui.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

system_actions.o: system_actions.c include/vortex_system_actions.h include/vortex_language.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

snippets.o: snippets.c include/vortex_snippets.h include/vortex_language.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

dialog.o: dialog.c include/vortex_dialog.h include/vortex_ui.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

platform/x11.o: platform/x11.c include/vortex_ui.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

test_language: test_language.o language.o
	$(CC) $(CFLAGS) $(WARNINGS) -o $@ $^

test_language.o: test_language.c include/vortex_language.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

test_catalog: test_catalog.o catalog.o
	$(CC) $(CFLAGS) $(WARNINGS) -o $@ $^

test_catalog.o: test_catalog.c include/vortex_catalog.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

test_actions: test_actions.o actions.o system_actions.o snippets.o dialog.o language.o platform/x11.o
	$(CC) $(CFLAGS) $(WARNINGS) -o $@ $^ $(LDLIBS)

test_actions.o: test_actions.c include/vortex_actions.h include/vortex_language.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

test_system_actions: test_system_actions.o system_actions.o snippets.o language.o
	$(CC) $(CFLAGS) $(WARNINGS) -o $@ $^

test_system_actions.o: test_system_actions.c include/vortex_system_actions.h include/vortex_language.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

test_snippets: test_snippets.o snippets.o language.o
	$(CC) $(CFLAGS) $(WARNINGS) -o $@ $^

test_snippets.o: test_snippets.c include/vortex_snippets.h include/vortex_language.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -c -o $@ $<

check: all test_language test_catalog test_actions test_system_actions test_snippets
	./test_language
	./test_catalog ../modules
	./test_actions
	./test_system_actions
	./test_snippets
	@for script in ../modules/*.vscript; do \
		./vortex-native --check "$$script" || exit 1; \
	done
	@test "$$(./vortex-native --list ../modules | wc -l)" -eq 10

clean:
	rm -f vortex-native test_language test_catalog test_actions test_system_actions test_snippets main.o catalog.o language.o actions.o system_actions.o snippets.o dialog.o test_language.o test_catalog.o test_actions.o test_system_actions.o test_snippets.o platform/x11.o

.PHONY: all check clean
