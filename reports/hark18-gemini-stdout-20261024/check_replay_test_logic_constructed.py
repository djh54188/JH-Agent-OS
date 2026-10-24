"""Hark-派单-18: exercises TestOriginalRun1Replay's logic on a CONSTRUCTED file (NOT the original).

The known line is prefixed to the committed 750b816 pair-node22-warm stdout and the test's pinned
size/digest/session are overridden for this process only. A pass here shows only that the replay
assertions work; it is not an original-byte replay. Usage (repo root):
    git show 750b81673ee5b3d68bcab6c2ac85d87a32b94d72:reports/gemini-node22-probe-20261023/evidence/pair-node22-warm/stdout.jsonl > pair.jsonl
    python reports/hark18-gemini-stdout-20261024/check_replay_test_logic_constructed.py pair.jsonl
"""
import hashlib, json, os, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
pair = Path(sys.argv[1]).read_bytes()
data = b"Rename failed with EPERM, retrying in 50ms (attempt 1/5)...\n" + pair
tmp = Path(tempfile.mkdtemp(prefix="hark18-constructed-")) / "constructed-run1.jsonl"
tmp.write_bytes(data)
os.environ["HARK18_ORIGINAL_STDOUT"] = str(tmp)
import test_hark18_gemini_stdout as T  # noqa: E402
T.ORIGINAL_BYTES, T.ORIGINAL_SHA256 = len(data), hashlib.sha256(data).hexdigest()
T.ORIGINAL_SESSION = json.loads(pair.split(b"\n")[0])["session_id"]
T.TestOriginalRun1Replay.__unittest_skip__ = False
r = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(T.TestOriginalRun1Replay))
print(json.dumps({"constructed_input": True, "pair_sha256": hashlib.sha256(pair).hexdigest(),
                  "constructed_sha256": T.ORIGINAL_SHA256, "ok": r.wasSuccessful()}))
