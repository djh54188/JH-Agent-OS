"""JH-019 layering tests (static).

The presentation layer must not reach past the application interface: no fetch
outside the data-access layer, no database, no tool launching, and no code that
decides task success. The transport must contain no business rules.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui"
GUI_FILES = sorted(GUI.glob("*"))
JS_FILES = sorted(GUI.glob("*.js"))


class TestPresentationLayering(unittest.TestCase):
    def test_expected_files_exist(self):
        names = {p.name for p in GUI_FILES}
        self.assertEqual(names, {"index.html", "styles.css", "data_access.js",
                                 "components.js", "views.js"})

    def test_only_the_data_access_layer_calls_fetch(self):
        for path in GUI_FILES:
            text = path.read_text(encoding="utf-8")
            if path.name == "data_access.js":
                self.assertIn("fetch(", text)
            else:
                self.assertNotIn("fetch(", text, path.name)

    def test_no_presentation_file_touches_a_database_or_a_process(self):
        for path in GUI_FILES:
            text = path.read_text(encoding="utf-8")
            for forbidden in ("sqlite", "sqlite3", "XMLHttpRequest", "require(",
                              "child_process", "spawn(", "exec("):
                self.assertNotIn(forbidden, text, f"{path.name}: {forbidden}")

    def test_pages_do_not_derive_success(self):
        """No comparison/assignment against a success literal anywhere in the UI."""
        patterns = [r'===\s*["\']succeeded["\']', r'==\s*["\']succeeded["\']',
                    r'=\s*["\']succeeded["\']', r'succeeded\s*=', r'is_success',
                    r'["\']成功["\']\s*=']
        for path in GUI_FILES:
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                self.assertIsNone(re.search(pattern, text), f"{path.name}: {pattern}")

    def test_pages_build_dom_without_html_injection(self):
        """No HTML is ever assembled from data (comments explaining the rule are fine)."""
        patterns = [r"\.innerHTML\s*=", r"\.outerHTML\s*=",
                    r"\.insertAdjacentHTML\s*\(", r"document\.write\s*\("]
        for path in JS_FILES:
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                self.assertIsNone(re.search(pattern, text), f"{path.name}: {pattern}")

    def test_index_loads_the_three_layers_in_order(self):
        html = (GUI / "index.html").read_text(encoding="utf-8")
        for name in ("data_access.js", "components.js", "views.js"):
            self.assertIn(name, html)
        self.assertLess(html.index("data_access.js"), html.index("components.js"))
        self.assertLess(html.index("components.js"), html.index("views.js"))

    def test_task_selection_is_explicit_not_text_scraped(self):
        """Selecting a task uses a button carrying the task id, not parsing card text."""
        views = (GUI / "views.js").read_text(encoding="utf-8")
        self.assertIn("task-action", views)
        self.assertIn("dataset.taskId", views)
        self.assertNotIn("match(/task_id", views)

    def test_components_have_no_pages_and_pages_have_no_fetch(self):
        components = (GUI / "components.js").read_text(encoding="utf-8")
        views = (GUI / "views.js").read_text(encoding="utf-8")
        self.assertNotIn("JH.api", components)           # components stay dumb
        self.assertIn("JH.api", views)                   # pages go through the DAL
        self.assertIn("JH.components", views)


class TestTransportHasNoBusinessRules(unittest.TestCase):
    def test_transport_delegates_to_the_application_interface(self):
        source = (ROOT / "src" / "gui_http.py").read_text(encoding="utf-8")
        self.assertIn("from src.app_api import", source)
        self.assertIn("self.api.", source)
        for forbidden in ("orchestrator", "Orchestrator", "ModelAdapter",
                          "sqlite3", "transition_task_state", "review_result"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_transport_only_serves_the_gui_directory(self):
        source = (ROOT / "src" / "gui_http.py").read_text(encoding="utf-8")
        self.assertIn("GUI_DIR", source)
        self.assertIn("越界路径", source)                  # traversal guard exists

    def test_app_interface_does_not_talk_to_sql_directly(self):
        """It reaches storage only through the storage interface: no driver
        import, no connection handle, no hand-written SQL. (The default database
        *filename* contains 'sqlite3' and is not a driver import.)"""
        source = (ROOT / "src" / "app_api.py").read_text(encoding="utf-8")
        for forbidden in ("import sqlite3", "_conn", "cur = ", "cursor(", "SELECT "):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertIn("open_readonly_storage", source)

    def test_read_method_is_the_only_addition_to_storage(self):
        """The new storage access is a read-only index query."""
        source = (ROOT / "src" / "storage.py").read_text(encoding="utf-8")
        self.assertIn("def list_tasks(", source)
        body = source.split("def list_tasks(", 1)[1].split("@atomic", 1)[0]
        self.assertIn("SELECT", body)
        for forbidden in ("INSERT", "UPDATE", "DELETE"):
            self.assertNotIn(forbidden, body, forbidden)


if __name__ == "__main__":
    unittest.main()
