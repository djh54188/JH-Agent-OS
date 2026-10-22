"""Multi-process helper for tests/test_hark12_gemini_admission.py (SYNTHETIC).

Each worker process builds its OWN adapter on the shared evidence root, waits
on a shared barrier, then submits the same request. Every real launch appends
one line to `launches.txt`, so the parent can count dispatches across processes.
No model, no real CLI: the launched program is fake_cli.py.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def worker(paths, barrier, queue):
    from src import agent_session_gemini as gem
    from src.agent_session import SessionAddress, SubmitRequest

    fake = os.path.join(ROOT, "tests", "fixtures", "gemini_cli", "fake_cli.py")
    config = gem.GeminiCliConfig(argv_prefix=(sys.executable, fake), cli_home=paths["home"],
                                 evidence_root=paths["evidence"],
                                 extra_env=(("FAKE_GEMINI_SCENARIO", paths["scenario"]),))

    def launcher(argv, **kwargs):
        fd = os.open(paths["launches"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        finally:
            os.close(fd)
        return gem.default_launcher(argv, **kwargs)

    adapter = gem.GeminiCliAgentSessionAdapter(config, launcher=launcher,
                                               version_probe=lambda cfg: "0.65.0")
    request = SubmitRequest(task_id="t-locate", attempt=1,
                            session=SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local",
                                                   session_id="new"),
                            instructions="Locate class RunState.", workspace=paths["workspace"],
                            idempotency_key="k-mp")
    barrier.wait(timeout=30)
    try:
        status = adapter.submit(request)
        run = adapter.inspect(status.run_id)
        if run is not None and run.process is not None:
            run.process.wait(timeout=60)
        queue.put(("ok", status.run_id, status.state, status.note))
    except Exception as exc:  # reported to the parent, which decides
        queue.put(("refused", type(exc).__name__, str(exc), None))
