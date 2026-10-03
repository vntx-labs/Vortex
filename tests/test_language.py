import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from modules.language import ScriptError, _run_action, parse_script, run_script


class LanguageParserTests(unittest.TestCase):
    def test_parses_nested_conditionals(self):
        program = parse_script(
            'call test.run\n'
            'if failed last\n'
            '    if empty value\n'
            '        return 1\n'
            '    else\n'
            '        return 0\n'
            '    end\n'
            'end\n'
        )
        self.assertEqual(
            [instruction.operation for instruction in program],
            ["call", "if", "if", "return", "else", "return", "end", "end"],
        )

    def test_loop_repeats_until_a_conditional_break(self):
        calls = []

        def fake_action(action, arguments, variables, host_app):
            self.assertEqual(host_app, "unused")
            calls.append(action)
            if action == "test.set":
                variables[arguments[0]] = arguments[1]
            if action == "test.increment":
                variables["count"] = str(int(variables["count"]) + 1)
            return 0, ""

        status = run_script(
            'call test.set count "0"\n'
            'loop\n'
            '    if equals count "3"\n'
            '        break\n'
            '    end\n'
            '    call test.increment\n'
            'end\n'
            'return 0\n',
            "unused",
            fake_action,
        )
        self.assertEqual(status, 0)
        self.assertEqual(calls, ["test.set", *["test.increment"] * 3])

    def test_rejects_break_outside_loop_and_else_on_loop(self):
        with self.assertRaisesRegex(ScriptError, "muss in einer Schleife stehen"):
            parse_script("break\n")
        with self.assertRaisesRegex(ScriptError, "unerwartetes 'else'"):
            parse_script("loop\nelse\nend\n")

    def test_rejects_unclosed_conditional(self):
        with self.assertRaisesRegex(ScriptError, "nicht mit 'end' geschlossen"):
            parse_script("if empty value\nreturn 0\n")

    def test_rejects_unknown_statement(self):
        with self.assertRaisesRegex(ScriptError, "unbekannter Befehl"):
            parse_script("execute arbitrary\n")

    def test_interpreter_runs_only_matching_branches(self):
        calls = []

        def fake_action(action, arguments, variables, host_app):
            self.assertEqual(host_app, "unused")
            calls.append((action, arguments))
            if action == "test.set":
                variables[arguments[0]] = arguments[1]
                return 0, ""
            if action == "test.fail":
                return 1, "expected"
            return 0, ""

        status = run_script(
            'call test.set answer "42"\n'
            'if empty answer\n'
            '    call test.record "wrong"\n'
            'else\n'
            '    call test.record "${answer}"\n'
            'end\n'
            'call test.fail\n'
            'if failed last\n'
            '    call test.record "${last_error}"\n'
            'end\n'
            'return 0\n',
            "unused",
            fake_action,
        )
        self.assertEqual(status, 0)
        self.assertEqual(
            calls,
            [
                ("test.set", ["answer", "42"]),
                ("test.record", ["42"]),
                ("test.fail", []),
                ("test.record", ["expected"]),
            ],
        )

    def test_backup_workflow_is_valid_dsl(self):
        script_path = Path(__file__).resolve().parents[1] / "modules" / "backup-sync.vscript"
        program = parse_script(script_path.read_text(encoding="utf-8"))
        self.assertGreater(len(program), 20)
        self.assertEqual(program[-1].operation, "return")

    def test_backup_workflow_cleans_preview_when_user_cancels(self):
        module_dir = Path(__file__).resolve().parents[1] / "modules"
        program = (module_dir / "backup-sync.vscript").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            selected_paths = iter([str(source), str(destination)])
            calls = []

            def fake_action(action, arguments, variables, host_app):
                self.assertEqual(host_app, "unused")
                calls.append(action)
                if action == "core.ensure_optional_tool":
                    return 0, ""
                if action == "core.pick_path":
                    variables[arguments[0]] = next(selected_paths)
                    return 0, ""
                if action == "ui.text_info":
                    variables[arguments[0]] = "1"
                    return 0, ""
                if action == "ui.confirm":
                    self.fail("Confirmation must not be shown after cancelling the preview.")
                if action == "process.capture":
                    Path(variables[arguments[0]]).write_text("preview", encoding="utf-8")
                    return 0, ""
                if action.startswith("core."):
                    return 0, ""
                return _run_action(action, arguments, variables, host_app)

            result = run_script(program, "unused", fake_action)
            self.assertEqual(result, 0)
            self.assertNotIn("process.progress", calls)
            self.assertFalse(any(root.glob("velos-backup-*")))

    def test_backup_workflow_reaches_confirmed_sync(self):
        module_dir = Path(__file__).resolve().parents[1] / "modules"
        program = (module_dir / "backup-sync.vscript").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            selected_paths = iter([str(source), str(destination)])
            calls = []

            def fake_action(action, arguments, variables, host_app):
                self.assertEqual(host_app, "unused")
                calls.append(action)
                if action == "core.ensure_optional_tool":
                    return 0, ""
                if action == "core.pick_path":
                    variables[arguments[0]] = next(selected_paths)
                    return 0, ""
                if action == "process.capture":
                    Path(variables[arguments[0]]).write_text("preview", encoding="utf-8")
                    return 0, ""
                if action == "ui.text_info":
                    variables[arguments[0]] = "0"
                    return 0, ""
                if action == "ui.confirm":
                    variables[arguments[0]] = "yes"
                    return 0, ""
                if action.startswith("core."):
                    return 0, ""
                return _run_action(action, arguments, variables, host_app)

            result = run_script(program, "unused", fake_action)
            self.assertEqual(result, 0)
            self.assertIn("process.progress", calls)
            self.assertIn("ui.info", calls)
            self.assertFalse(any(root.glob("velos-backup-*")))

    def test_progress_action_forwards_rsync_output_to_dialog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rsync = root / "rsync"
            yad = root / "yad"
            dialog_output = root / "dialog-output"
            rsync.write_text(
                "#!/bin/sh\nprintf 'Transfer progress\\n' >&2\n",
                encoding="utf-8",
            )
            yad.write_text(
                "#!/bin/sh\ncat > \"$YAD_CAPTURE\"\n",
                encoding="utf-8",
            )
            rsync.chmod(0o700)
            yad.chmod(0o700)
            environment = {
                "PATH": f"{root}:{os.environ['PATH']}",
                "YAD_CAPTURE": str(dialog_output),
            }
            with patch.dict("os.environ", environment):
                status, error = _run_action(
                    "process.progress",
                    ["source", "destination"],
                    {"source": str(root / "source"), "destination": str(root / "destination")},
                    "unused",
                )
            self.assertEqual((status, error), (0, ""))
            self.assertIn("Transfer progress", dialog_output.read_text(encoding="utf-8"))

    def test_runtime_cli_executes_manifest_selected_dsl_entry(self):
        modules = Path(__file__).resolve().parents[1] / "modules"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sample.vmod").write_text(
                'module sample\nname "Sample"\ndescription "Test"\nicon "x"\n'
                'backend dsl\norder 1\nentry "sample.vscript"\n',
                encoding="utf-8",
            )
            (root / "sample.vscript").write_text("return 7\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(modules / "runtime.py"),
                    "run",
                    str(root),
                    "sample",
                    "/unused/launcher",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 7, result.stderr)

    def test_port_process_workflow_stops_after_dialog_cancel(self):
        module_dir = Path(__file__).resolve().parents[1] / "modules"
        program = (module_dir / "port-process.vscript").read_text(encoding="utf-8")
        calls = []

        def fake_action(action, arguments, variables, _host_app):
            self.assertEqual(_host_app, "unused")
            calls.append(action)
            if action == "ui.entry":
                variables[arguments[0]] = ""
            return 0, ""

        self.assertEqual(run_script(program, "unused", fake_action), 0)
        self.assertEqual(calls, ["ui.entry"])

    def test_port_process_workflow_sends_term_before_confirmed_kill(self):
        module_dir = Path(__file__).resolve().parents[1] / "modules"
        program = (module_dir / "port-process.vscript").read_text(encoding="utf-8")
        calls = []

        def fake_action(action, arguments, variables, host_app):
            self.assertEqual(host_app, "unused")
            calls.append((action, arguments))
            if action == "ui.entry":
                variables[arguments[0]] = "8080"
            elif action == "process.listener_pids":
                variables[arguments[0]] = "321"
            elif action == "ui.select_port_process":
                variables[arguments[0]] = "321"
            elif action == "process.terminate_listener":
                variables["listener_still_running"] = "yes"
            elif action == "ui.confirm":
                variables[arguments[0]] = "yes"
            return 0, ""

        self.assertEqual(run_script(program, "unused", fake_action), 0)
        actions = [action for action, _ in calls]
        self.assertLess(
            actions.index("ui.confirm"),
            actions.index("process.terminate_listener"),
        )
        self.assertLess(
            actions.index("process.terminate_listener"),
            actions.index("process.kill_selected"),
        )
        self.assertEqual(
            calls[-1], ("process.kill_selected", ["321", "force_kill"])
        )

    def test_port_process_workflow_does_not_force_kill_after_term_succeeds(self):
        module_dir = Path(__file__).resolve().parents[1] / "modules"
        program = (module_dir / "port-process.vscript").read_text(encoding="utf-8")
        calls = []

        def fake_action(action, arguments, variables, _host_app):
            self.assertEqual(_host_app, "unused")
            calls.append(action)
            if action == "ui.entry":
                variables[arguments[0]] = "8080"
            elif action == "process.listener_pids":
                variables[arguments[0]] = "321"
            elif action == "ui.select_port_process":
                variables[arguments[0]] = "321"
            elif action == "ui.confirm":
                variables[arguments[0]] = "yes"
            elif action == "process.terminate_listener":
                variables["listener_still_running"] = "no"
            return 0, ""

        self.assertEqual(run_script(program, "unused", fake_action), 0)
        self.assertEqual(calls.count("ui.confirm"), 1)
        self.assertNotIn("process.kill_selected", calls)

    def test_listener_pid_action_validates_port_and_deduplicates_results(self):
        status, message = _run_action(
            "process.listener_pids", ["pids", "65536"], {}, "unused"
        )
        self.assertEqual(status, 1)
        self.assertIn("gültigen Port", message)

        from modules import language

        completed = subprocess.CompletedProcess(
            ["lsof"], 0, stdout="456\n123\n456\n", stderr=""
        )
        with patch.object(language.shutil, "which", return_value="/usr/bin/lsof"):
            with patch.object(language.subprocess, "run", return_value=completed) as run:
                variables = {}
                status, message = _run_action(
                    "process.listener_pids", ["pids", "8080"], variables, "unused"
                )
        self.assertEqual((status, message), (0, ""))
        self.assertEqual(variables["pids"], "123\n456")
        self.assertEqual(run.call_args.args[0][3], "-iTCP:8080")

    def test_terminate_listener_rechecks_port_before_signalling(self):
        from modules import language

        with patch.object(language, "_listener_pids", return_value=(0, "123\n")):
            with patch.object(language.os, "kill") as kill:
                status, error = _run_action(
                    "process.terminate_listener",
                    ["8080", "456", "confirmed"],
                    {"confirmed": "yes"},
                    "unused",
                )
        self.assertEqual(status, 1)
        self.assertIn("blockiert Port 8080 nicht mehr", error)
        kill.assert_not_called()

    def test_terminate_listener_sends_sigterm_and_reports_if_still_running(self):
        from modules import language

        with patch.object(language, "_listener_pids", return_value=(0, "123\n")):
            with patch.object(
                language.os,
                "kill",
                side_effect=[None] * 22,
            ) as kill:
                with patch.object(language.time, "sleep") as sleep:
                    variables = {"confirmed": "yes"}
                    status, error = _run_action(
                        "process.terminate_listener",
                        ["8080", "123", "confirmed"],
                        variables,
                        "unused",
                    )
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(variables["listener_still_running"], "yes")
        self.assertEqual(kill.call_count, 22)
        self.assertEqual(kill.call_args_list[0].args, (123, 0))
        self.assertEqual(kill.call_args_list[1].args, (123, language.signal.SIGTERM))
        self.assertEqual(sleep.call_count, 20)

    def test_terminate_listener_requires_explicit_confirmation(self):
        from modules import language

        with patch.object(language, "_listener_pids", return_value=(0, "123\n")):
            with patch.object(language.os, "kill") as kill:
                status, error = _run_action(
                    "process.terminate_listener",
                    ["8080", "123", "confirmed"],
                    {"confirmed": "no"},
                    "unused",
                )
        self.assertEqual(status, 1)
        self.assertIn("nicht ausdrücklich bestätigt", error)
        kill.assert_not_called()

    def test_kill_selected_requires_confirmation_and_targets_only_selected_pid(self):
        from modules import language

        with patch.object(language.os, "kill") as kill:
            status, error = _run_action(
                "process.kill_selected", ["123", "confirmed"], {}, "unused"
            )
        self.assertEqual(status, 1)
        self.assertIn("nicht ausdrücklich bestätigt", error)
        kill.assert_not_called()

        with patch.object(language.os, "kill") as kill:
            status, error = _run_action(
                "process.kill_selected",
                ["123", "confirmed"],
                {"confirmed": "yes"},
                "unused",
            )
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(kill.call_args_list[0].args, (123, 0))
        self.assertEqual(
            kill.call_args_list[1].args, (123, language.signal.SIGKILL)
        )

    def test_snippet_store_round_trips_and_mutates_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {
                "HOME": str(root / "home"),
            }
            with patch.dict(os.environ, environment):
                status, error = _run_action("snippets.initialize", [], {}, "unused")
                self.assertEqual((status, error), (0, ""))
                store = root / "home" / ".local" / "share" / "velos" / "snippets.tsv"
                self.assertEqual(store.stat().st_mode & 0o777, 0o600)
                self.assertEqual(store.parent.stat().st_mode & 0o777, 0o700)

                self.assertEqual(
                    _run_action("snippets.save", ["new", "First", "line 1\nline 2"], {}, "unused"),
                    (0, ""),
                )
                self.assertEqual(
                    _run_action("snippets.save", ["new", "Second", "SQL"], {}, "unused"),
                    (0, ""),
                )
                with store.open("a", encoding="ascii") as output:
                    output.write("invalid-row\n")

                variables = {}
                self.assertEqual(
                    _run_action("snippets.list", ["items"], variables, "unused"),
                    (0, ""),
                )
                self.assertEqual(
                    json.loads(variables["items"]),
                    [["1", "First"], ["2", "Second"]],
                )
                self.assertEqual(
                    _run_action("snippets.get", ["title", "body", "1"], variables, "unused"),
                    (0, ""),
                )
                self.assertEqual((variables["title"], variables["body"]), ("First", "line 1\nline 2"))
                self.assertEqual(
                    _run_action("snippets.save", ["1", "Renamed", "updated"], {}, "unused"),
                    (0, ""),
                )
                self.assertEqual(
                    _run_action("snippets.delete", ["2"], {}, "unused"),
                    (0, ""),
                )
                variables = {}
                self.assertEqual(
                    _run_action("snippets.list", ["items"], variables, "unused"),
                    (0, ""),
                )
                self.assertEqual(json.loads(variables["items"]), [["1", "Renamed"]])

    def test_snippet_initialization_refuses_a_symlinked_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store_dir = root / "home" / ".local" / "share" / "velos"
            store_dir.mkdir(parents=True)
            target = root / "target"
            target.write_text("do not change\n", encoding="utf-8")
            (store_dir / "snippets.tsv").symlink_to(target)
            with patch.dict(os.environ, {"HOME": str(root / "home")}):
                status, error = _run_action("snippets.initialize", [], {}, "unused")
            self.assertEqual(status, 1)
            self.assertIn("nicht vorbereitet", error)
            self.assertEqual(target.read_text(encoding="utf-8"), "do not change\n")

    def test_snippet_list_dialog_keeps_titles_as_single_arguments(self):
        from modules import language

        title = "A title\n--button=Unexpected:0"
        rows = json.dumps([["1", title]], ensure_ascii=False)
        result = subprocess.CompletedProcess(
            ["yad"], 0, stdout="1\n", stderr=""
        )
        with patch.object(language.subprocess, "run", return_value=result) as run:
            variables = {}
            status, error = _run_action(
                "ui.list",
                [
                    "selected",
                    "response",
                    "Snippet-Hüter",
                    "Description",
                    '["Bearbeiten:0","Schließen:1"]',
                    rows,
                ],
                variables,
                "unused",
            )
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(variables, {"selected": "1", "response": "0"})
        self.assertIn(title, run.call_args.args[0])

    def test_snippet_editor_uses_private_temp_file_and_returns_saved_text(self):
        from modules import language

        def fake_yad(command, **kwargs):
            self.assertTrue(kwargs["capture_output"])
            self.assertFalse(kwargs["check"])
            body_path = Path(next(value.split("=", 1)[1] for value in command if value.startswith("--filename=")))
            self.assertEqual(body_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(body_path.read_text(encoding="utf-8"), "initial")
            return subprocess.CompletedProcess(command, 0, stdout="edited", stderr="")

        with patch.object(language.subprocess, "run", side_effect=fake_yad):
            variables = {}
            status, error = _run_action(
                "ui.edit_text",
                ["body", "saved", "Title", "Prompt", "initial"],
                variables,
                "unused",
            )
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(variables, {"body": "edited", "saved": "yes"})

    def test_clipboard_action_passes_content_on_stdin(self):
        from modules import language

        result = subprocess.CompletedProcess(["wl-copy"], 0, stdout="", stderr="")
        with patch.object(language.shutil, "which", side_effect=lambda name: "/usr/bin/wl-copy" if name == "wl-copy" else None):
            with patch.object(language.subprocess, "run", return_value=result) as run:
                status, error = _run_action(
                    "system.clipboard", ["secret code"], {}, "unused"
                )
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(run.call_args.args[0], ["wl-copy"])
        self.assertEqual(run.call_args.kwargs["input"], "secret code")

    def test_snippet_workflow_supports_create_edit_copy_delete_and_exit(self):
        module_dir = Path(__file__).resolve().parents[1] / "modules"
        program = (module_dir / "snippet-manager.vscript").read_text(encoding="utf-8")
        self.assertGreater(len(parse_script(program)), 30)
        menus = iter(
            [
                ("", "2"),
                ("1", "0"),
                ("1", "4"),
                ("1", "3"),
                ("", "1"),
            ]
        )
        events = []

        def fake_action(action, arguments, variables, host_app):
            events.append((action, arguments))
            if action in {"snippets.initialize", "snippets.list", "snippets.get", "snippets.save", "snippets.delete"}:
                return _run_action(action, arguments, variables, host_app)
            if action == "ui.list":
                selected, response = next(menus)
                variables[arguments[0]] = selected
                variables[arguments[1]] = response
            elif action == "ui.entry":
                variables[arguments[0]] = "Example" if arguments[0] == "title" else "Renamed"
                if len(arguments) == 5:
                    variables[arguments[1]] = "yes"
            elif action == "ui.edit_text":
                variables[arguments[0]] = "example body" if arguments[0] == "body" else "edited body"
                variables[arguments[1]] = "yes"
            elif action == "ui.confirm":
                variables[arguments[0]] = "yes"
            elif action == "system.clipboard":
                self.assertEqual(arguments, ["edited body"])
            return 0, ""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {
                "HOME": str(root / "home"),
                "XDG_DATA_HOME": str(root / "data"),
            }
            with patch.dict(os.environ, environment):
                self.assertEqual(run_script(program, "unused", fake_action), 0)
                variables = {}
                self.assertEqual(
                    _run_action("snippets.list", ["items"], variables, "unused"),
                    (0, ""),
                )
                self.assertEqual(json.loads(variables["items"]), [])

        action_names = [action for action, _ in events]
        self.assertEqual(action_names.count("ui.list"), 5)
        self.assertIn("system.clipboard", action_names)
        self.assertIn("snippets.delete", action_names)


if __name__ == "__main__":
    unittest.main()
