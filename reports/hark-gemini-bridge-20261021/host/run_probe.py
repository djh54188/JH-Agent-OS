"""Run the real Gemini CLI against the synthetic endpoint. argv: name script.json [extra cli args...]
Env is built from scratch (no inherited credentials). Writes NAME.stdout.jsonl, NAME.stderr.txt, NAME.server.jsonl"""
import json, os, subprocess, sys, time, socket
here = os.path.dirname(os.path.abspath(__file__))
name, script = sys.argv[1], sys.argv[2]; extra = sys.argv[3:]
cfg = json.load(open(os.path.join(here, "probe_env.json")))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
log = os.path.join(here, name + ".server.jsonl")
if os.path.exists(log): os.remove(log)
srv = subprocess.Popen([sys.executable, os.path.join(here, "fake_gemini.py"), str(port), script, log])
time.sleep(0.8)
env = {"PATH": "/usr/bin:/bin", "HOME": cfg["home"], "GEMINI_CLI_HOME": cfg["home"],
       "GOOGLE_GEMINI_BASE_URL": f"http://127.0.0.1:{port}", "GEMINI_API_KEY": "offline-placeholder-not-a-credential",
       "GEMINI_CLI_NO_RELAUNCH": "true", "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
       "NO_PROXY": "127.0.0.1,localhost", "NO_COLOR": "1"}
env.update(cfg.get("env", {}))
argv = ["node", cfg["cli"]] + cfg.get("args", []) + extra
try:
    p = subprocess.run(argv, cwd=cfg["cwd"], env=env, capture_output=True, timeout=cfg.get("timeout", 90))
    rc = p.returncode; out, err = p.stdout, p.stderr
except subprocess.TimeoutExpired as e:
    rc = "timeout"; out, err = e.stdout or b"", e.stderr or b""
finally:
    srv.kill(); srv.wait()
open(os.path.join(here, name + ".stdout.jsonl"), "wb").write(out)
open(os.path.join(here, name + ".stderr.txt"), "wb").write(err)
print("rc", rc, "stdout_bytes", len(out), "stderr_bytes", len(err))
