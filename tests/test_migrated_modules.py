import tarfile
import tempfile
import unittest
from pathlib import Path

from modules.csv_workspace import search_csv, sort_csv
from modules.language import parse_script, run_script
from modules.module_actions import (
    _apply_replacements,
    _extract_safe_archive,
    _replace_plan,
)
from modules.runtime import load_modules
from modules.web_studio import make_document, text_html


ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "modules"
MIGRATED = {
    "csv-workspace": "gui.csv_workspace",
    "file-tools": "ui.list",
    "focus-code-editor": "core.ensure_python_feature",
    "local-safe": "ui.list",
    "local-server": "module.local_server",
    "web-studio": "gui.web_studio",
}


class MigratedModuleTests(unittest.TestCase):
    def test_all_requested_modules_resolve_to_dsl_entries(self):
        modules = {module.module_id: module for module in load_modules(str(MODULES))}
        for module_id, action in MIGRATED.items():
            with self.subTest(module=module_id):
                module = modules[module_id]
                self.assertEqual(module.backend, "dsl")
                self.assertTrue(module.entry.endswith(".vscript"))
                program = parse_script((MODULES / module.entry).read_text(encoding="utf-8"))
                first_call = next(
                    instruction for instruction in program
                    if instruction.operation == "call"
                )
                self.assertEqual(first_call.arguments[0], action)
                self.assertFalse((MODULES / f"{module_id}.sh").exists())

    def test_file_tools_dsl_routes_selected_tool_and_returns_to_menu(self):
        script = (MODULES / "file-tools.vscript").read_text(encoding="utf-8")
        actions = []
        selections = iter(["🔁  Regex-Massen-Ersetzen", ""])

        def fake_action(action, arguments, variables, host_app):
            self.assertEqual(host_app, "unused")
            actions.append((action, arguments))
            if action == "ui.list":
                variables[arguments[0]] = next(selections)
                variables[arguments[1]] = "0"
            return 0, ""

        self.assertEqual(run_script(script, "unused", fake_action), 0)
        self.assertEqual(
            [action for action, _ in actions],
            ["ui.list", "module.file_tools.replace", "ui.list"],
        )

    def test_local_safe_dsl_passes_selection_to_safe_action(self):
        script = (MODULES / "local-safe.vscript").read_text(encoding="utf-8")
        actions = []
        selections = iter(["Ordner verschlüsseln", ""])

        def fake_action(action, arguments, variables, host_app):
            self.assertEqual(host_app, "unused")
            actions.append((action, arguments))
            if action == "ui.list":
                variables[arguments[0]] = next(selections)
                variables[arguments[1]] = "0"
            return 0, ""

        self.assertEqual(run_script(script, "unused", fake_action), 0)
        self.assertEqual(actions[1], ("module.local_safe", ["Ordner verschlüsseln"]))

    def test_gui_support_files_are_local_and_web_studio_is_offline(self):
        for filename in (
            "csv_workspace.py", "csv_table_editor.py",
            "focus_code_editor.py", "web_studio.py",
        ):
            self.assertTrue((MODULES / filename).is_file(), filename)
        builder = (MODULES / "web_studio_builder.html").read_text(encoding="utf-8")
        self.assertIn("velos-export://save/", builder)
        self.assertIn("restoreAutosave()", builder)
        self.assertIn("indexedDB", builder)

    def test_web_studio_text_export_escapes_content_and_builds_standalone_html(self):
        document = make_document("<title>", text_html("<script>bad()</script>"), "Dark Tech")
        self.assertIn("&lt;title&gt;", document)
        self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", document)
        self.assertIn('<meta charset="utf-8">', document)
        self.assertIn("background:#0b1220", document)

    def test_regex_replace_plan_and_apply(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "one.txt"
            second = root / "two.txt"
            first.write_text("hello one\n", encoding="utf-8")
            second.write_text("hello two\n", encoding="utf-8")
            entries, changes = _replace_plan(
                "Dateiinhalte ersetzen", str(root), "*.txt", "hello", "goodbye"
            )
            self.assertEqual(len(entries), 2)
            self.assertEqual(len(changes), 2)
            _apply_replacements("Dateiinhalte ersetzen", changes)
            self.assertEqual(first.read_text(encoding="utf-8"), "goodbye one\n")
            self.assertEqual(first.with_name("one.txt.velos-backup").read_text(encoding="utf-8"), "hello one\n")

    def test_csv_search_and_numeric_sort(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.csv"
            source.write_text("Name,Count\nalpha,10\nbeta,2\n", encoding="utf-8")
            searched = root / "searched.csv"
            sorted_path = root / "sorted.csv"
            search_csv(str(source), "BETA", str(searched))
            sort_csv(str(source), "Count", False, str(sorted_path))
            self.assertIn("beta,2", searched.read_text(encoding="utf-8-sig"))
            self.assertEqual(
                sorted_path.read_text(encoding="utf-8-sig").splitlines(),
                ["Name,Count", "beta,2", "alpha,10"],
            )

    def test_safe_archive_extraction_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "malicious.tar"
            with tarfile.open(archive_path, "w") as archive:
                payload = root / "payload"
                payload.write_text("bad", encoding="utf-8")
                archive.add(payload, arcname="../escape")
            with self.assertRaisesRegex(ValueError, "ungültige Pfade"):
                _extract_safe_archive(str(archive_path), str(root), "restore", True)
            self.assertFalse((root.parent / "escape").exists())

    def test_safe_archive_extracts_regular_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "safe.tar"
            source_dir = root / "project"
            source_dir.mkdir()
            (source_dir / "index.html").write_text("offline", encoding="utf-8")
            with tarfile.open(archive_path, "w") as archive:
                archive.add(source_dir, arcname="project")
            result = _extract_safe_archive(str(archive_path), str(root), "safe.tar.gz.gpg", True)
            self.assertEqual(Path(result, "index.html").read_text(encoding="utf-8"), "offline")


if __name__ == "__main__":
    unittest.main()
