"""Restart helper for tests/test_hark13_registry_loss.py (SYNTHETIC).

Runs in a freshly spawned interpreter: builds a NEW adapter on an evidence root
whose registry was lost and submits the same key. The launcher only counts (no
process, no model). Reports (outcome, exception type or run id, detail, launches).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def worker(paths, queue):
    from src import agent_session_gemini as gem
    from src.agent_session import SessionAddress, SubmitRequest

    launches = []
    config = gem.GeminiCliConfig(argv_prefix=("gemini-not-started",), cli_home=paths["home"],
                                 evidence_root=paths["evidence"])
    adapter = gem.GeminiCliAgentSessionAdapter(config, launcher=lambda argv, **kw: launches.append(argv),
                                               version_probe=lambda cfg: "0.65.0")
    request = SubmitRequest(task_id="t", attempt=1,
                            session=SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local",
                                                   session_id="new"),
                            instructions="Locate x.", workspace=paths["workspace"], idempotency_key="K")
    try:
        status = adapter.submit(request)
        queue.put(("dispatched", status.run_id, status.state, len(launches)))
    except Exception as exc:
        queue.put(("refused", type(exc).__name__, str(exc)[:300], len(launches)))
