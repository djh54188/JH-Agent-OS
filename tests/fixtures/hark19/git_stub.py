"""Hark-派单-19 test stand-in for git (SYNTHETIC; tests only). Passes every call to the real git
(HARK19_REAL_GIT) unless HARK19_GIT_FAULT names a fault for this subcommand:
  fail:<sub>                 exit 1 (sub = checkout-b | add | commit | post-commit-rev-parse)
  noop:commit                exit 0 without committing
  preparent:commit           an extra empty commit first, then the real commit
  extra:commit               add stray-extra.txt at the repo root, then the real commit
  mutate:commit              change the staged run-log.txt bytes, then the real commit
  dirty:commit               leave an untracked file, then the real commit
  rebind:commit              real commit, then detach HEAD and move the branch back to its parent
Only the repository named by -C is touched; state lives in HARK19_STUB_STATE."""
import os, subprocess, sys

REAL = os.environ["HARK19_REAL_GIT"]
FAULTS = set(filter(None, os.environ.get("HARK19_GIT_FAULT", "").split(",")))
STATE = os.environ.get("HARK19_STUB_STATE", "")
EVDIR = "reports/hark18-gemini-stdout-20261024/windows"


def real(args, cwd=None):
    return subprocess.run([REAL] + args, cwd=cwd).returncode


def main():
    args = sys.argv[1:]
    repo, sub, i = None, None, 0
    while i < len(args):
        if args[i] == "-c":
            i += 2; continue
        if args[i] == "-C":
            repo = args[i + 1]; i += 2; continue
        sub = args[i]; break
    name = "checkout-b" if sub == "checkout" and "-b" in args else sub
    committed = STATE and os.path.exists(os.path.join(STATE, "committed"))
    if f"fail:{name}" in FAULTS or (sub == "rev-parse" and committed and "fail:post-commit-rev-parse" in FAULTS):
        sys.stderr.write(f"git stub: injected failure for {name}\n"); return 1
    if sub == "commit":
        if "noop:commit" in FAULTS:
            return 0
        if "preparent:commit" in FAULTS:
            env = dict(os.environ, GIT_AUTHOR_NAME="stub", GIT_AUTHOR_EMAIL="stub@invalid",
                       GIT_COMMITTER_NAME="stub", GIT_COMMITTER_EMAIL="stub@invalid")
            pre = subprocess.run([REAL, "-C", repo, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "stub pre"],
                                 capture_output=True, text=True, env=env, check=True).stdout.strip()
            real(["-C", repo, "update-ref", "HEAD", pre])   # index (staged evidence) untouched
        if "extra:commit" in FAULTS:
            open(os.path.join(repo, "stray-extra.txt"), "w").write("extra\n")
            real(["-C", repo, "add", "stray-extra.txt"])
        if "mutate:commit" in FAULTS:
            with open(os.path.join(repo, EVDIR, "run-log.txt"), "ab") as fh:
                fh.write(b"tampered\n")
            real(["-C", repo, "add", EVDIR])
        if "dirty:commit" in FAULTS:
            open(os.path.join(repo, "stray-untracked.txt"), "w").write("x\n")
    code = real(args)
    if sub == "commit" and code == 0:
        if STATE:
            open(os.path.join(STATE, "committed"), "w").close()
        if "rebind:commit" in FAULTS:
            branch = subprocess.run([REAL, "-C", repo, "symbolic-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
            real(["-C", repo, "checkout", "-q", "--detach"])
            real(["-C", repo, "update-ref", branch, "HEAD^"])
    return code


sys.exit(main())
