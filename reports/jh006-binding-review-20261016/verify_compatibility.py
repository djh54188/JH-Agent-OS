"""Legacy database upgrade/readonly proof in isolated synthetic temporary DBs."""
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import types
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_jh006_capture_binding import _BindCase
from src.storage import TaskStorage
from src.capture_binding import get_capture_binding


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(db):
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {name: [tuple(r) for r in db.execute('SELECT * FROM "' + name + '"')] for name in names}


def main():
    legacy = types.ModuleType("jh006_legacy_storage")
    legacy.__file__ = str(ROOT / "src/storage.py")
    code = subprocess.check_output(["git", "show", "ec103a0:src/storage.py"], cwd=ROOT)
    exec(compile(code, legacy.__file__, "exec"), legacy.__dict__)
    case = _BindCase(); case.setUp()
    try:
        original = case.case / "legacy.sqlite3"
        old = legacy.TaskStorage(original)
        old.create_task(case._payload())
        before_rows = rows(old._conn)
        old.close()
        copied = case.case / "upgrade-copy.sqlite3"
        shutil.copyfile(original, copied)
        before_hash = digest(copied)
        ro = TaskStorage(copied, read_only=True)
        assert rows(ro._conn) == before_rows
        assert get_capture_binding(ro, "task-missing", "delivery-missing") is None
        ro.close()
        assert digest(copied) == before_hash
        upgraded = TaskStorage(copied)
        after_rows = rows(upgraded._conn)
        assert {name:after_rows[name] for name in before_rows} == before_rows
        assert after_rows["capture_bindings"] == []
        indexes = [r[1] for r in upgraded._conn.execute("PRAGMA index_list(capture_bindings)")]
        assert "capture_binding_call_unique" in indexes and "capture_binding_item_unique" in indexes
        upgraded.close()
        # Existing database of the first unverified implementation also gains indexes.
        first = TaskStorage(copied)
        first._conn.execute("DROP INDEX capture_binding_call_unique")
        first._conn.execute("DROP INDEX capture_binding_item_unique")
        first._conn.commit(); first.close()
        reopened = TaskStorage(copied)
        restored = [r[1] for r in reopened._conn.execute("PRAGMA index_list(capture_bindings)")]
        assert "capture_binding_call_unique" in restored and "capture_binding_item_unique" in restored
        reopened.close()
        result = {"passed":True,"source_baseline":"ec103a0","synthetic_data":True,
                  "legacy_tables":len(before_rows),"legacy_rows_unchanged":True,
                  "readonly_bytes_unchanged":True,"new_table_empty":True,
                  "unique_indexes_present":True,"early_candidate_table_index_upgrade":True}
        print(json.dumps(result,ensure_ascii=False))
        return result
    finally:
        case.doCleanups()


if __name__ == "__main__":
    main()
