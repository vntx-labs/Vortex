# Vortex modules

The launcher scans this directory for `.vmod` files. Each definition uses one
directive per line:

```text
module my-module
name "Module name"
description "Short description"
icon "applications-system-symbolic"
backend dsl
entry "my_module.vscript"
order 110
```

Text values with spaces must be quoted. Module IDs use lowercase letters,
numbers, and hyphens. Lower `order` values appear first in the home screen.
`backend` can be `shell`, `python`, or `dsl`. Python entries are started as
separate processes. DSL entries use the line-oriented Vortex workflow language
in `language.py`; its `call`, `if`, `else`, `end`, and `return` statements
invoke an allowlisted set of host, file, dialog, and process actions. Entry
files must stay inside this directory and use the matching `.sh`, `.py`, or
`.vscript` extension.

All six remaining shell-backed modules have been migrated to DSL manifests:
CSV Workspace, File Tools, Focus Code Editor, Local Safe, Local Server, and Web
Studio. GTK-/WebKit-native interfaces remain in local helper files:
`csv_table_editor.py`, `focus_code_editor.py`, `web_studio.py`, and the
offline `web_studio_builder.html` application. These helpers are launched only
by allowlisted DSL actions; none sources or calls the retired shell module
files.

The workflow language supports `call ACTION ...`, `if empty NAME`,
`if failed last`, `if declined NAME`, `if equals NAME VALUE`, `else`, `end`,
`loop`, `break`, and `return [STATUS]`.
Quoted values are tokenized without shell evaluation; `${name}` interpolates
previously set workflow variables. Host actions are called through the
launcher, and file, process, and dialog operations use named interpreter
actions rather than `eval` or shell command strings.

Available actions:

- `core.ensure_optional_tool COMMAND TITLE PACKAGE`
- `core.ensure_python_feature markdown|gtksourceview4|webkit2`
- `core.pick_path VARIABLE file|directory TITLE`
- `core.show_error TEXT`
- `fs.realpath VARIABLE`, `fs.assert_disjoint SOURCE DESTINATION`
- `fs.temp VARIABLE PREFIX`, `fs.compose_preview OUTPUT LOG SOURCE DESTINATION`
- `fs.tail FILE VARIABLE LINE_COUNT`, `fs.remove VARIABLE...`
- `process.capture LOG TIMEOUT_SECONDS ARG...`, `process.progress SOURCE DESTINATION`
- `process.ensure_port_tool`, `process.validate_port PORT`, `process.listener_pids RESULT PORT`
- `process.terminate_listener PORT PID CONFIRMATION`, `process.kill_selected PID CONFIRMATION`
- `ui.entry RESULT [STATUS] TITLE PROMPT DEFAULT`, `ui.select_port_process RESULT PORT PIDS`
- `ui.list SELECTED RESPONSE TITLE DESCRIPTION BUTTONS_JSON ROWS_JSON`
- `ui.edit_text RESULT SAVED TITLE DESCRIPTION CONTENT`
- `snippets.initialize`, `snippets.list RESULT`, `snippets.get TITLE BODY ID`
- `snippets.save ID|new TITLE BODY`, `snippets.delete ID`, `system.clipboard TEXT`
- `ui.text_info RESULT FILE TITLE BUTTON...`, `ui.confirm RESULT TITLE TEXT`
- `ui.info TITLE TEXT`, `ui.error TITLE TEXT`
- `module.file_tools.replace`, `module.file_tools.convert`
- `module.local_safe ACTION`, `module.local_server`
- `gui.csv_workspace`, `gui.focus_code_editor`, `gui.web_studio`

The native C runtime is being built alongside the current Python launcher.
It now has basic native file/list/text dialogs plus selected filesystem,
process, and snippet-store actions, but it is not feature-complete. Module
manifests and Python helpers remain in place until equivalent native
implementations and regression tests are available.

See `backup-sync.vscript` and `snippet-manager.vscript` for complete workflow examples.
