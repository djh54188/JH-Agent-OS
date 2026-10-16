from pathlib import Path
import subprocess,time,json
ROOT=Path(r"D:\JH-Agent OS\data\worktrees\n1-artifact-entry")
OUT=Path(r"D:\JH-Agent OS\reports\jh004-real-host-20261014")
EXE=Path(r"D:\npm-global\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe")
args=[str(EXE),"exec","-C",str(ROOT),"--sandbox","workspace-write","--approve-for-me","--ephemeral","--json","--output-last-message",str(OUT/"host-final.txt"),"-"]
start=time.monotonic()
with (OUT/"host-events.jsonl").open("wb") as stdout,(OUT/"host-stderr.txt").open("wb") as stderr:
 p=subprocess.Popen(args,cwd=ROOT,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr)
 (OUT/"host-launch.json").write_text(json.dumps({"pid":p.pid,"args":args,"timeout_seconds":900,"config_changes":False,"sandbox":"workspace-write","approval":"auto review"},indent=2),encoding="utf-8")
 p.stdin.write((OUT/"host-prompt.txt").read_bytes());p.stdin.close()
 try: code=p.wait(timeout=900);timed_out=False
 except subprocess.TimeoutExpired:
  timed_out=True
  # p still owns this live process handle; never operate on a stale recorded PID.
  if p.poll() is None:subprocess.run(["taskkill","/PID",str(p.pid),"/T","/F"],capture_output=True)
  code=p.wait(timeout=30)
result={"exit_code":code,"timed_out":timed_out,"elapsed_seconds":round(time.monotonic()-start,3)}
(OUT/"host-process-result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps(result))
