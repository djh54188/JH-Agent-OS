"""Hark-派单-21 task01: export src/ of the fixed source commit into a NEW plain directory.

This is only the documented, checkable way to prepare the target directory of the
task package. It is not a worktree, not a sandbox and not an export platform:
  * reads the fixed commit with `git ls-tree` / `git cat-file` (object bytes,
    no checkout, no smudge filters, no line-ending conversion);
  * writes every regular file under src/ byte for byte into --out, which must not
    exist yet (or be an empty directory);
  * refuses symlinks, submodules and anything that is not a regular blob;
  * recomputes the Git blob id of every written file from the bytes on disk and
    compares it with the tree entry; the target file must be the pinned blob;
  * writes a manifest (commit, full path set, size, sha256, blob) to --manifest,
    which must be OUTSIDE --out, so nothing but source enters the task directory.

No tests/, docs/, reports/, .git, task text, review baseline or checker code is
written into --out. Read-only access to the host tools is the host's job (admin
policy of the adapter), not a property of this directory.

Exit codes: 0 exported and verified; 2 could not export (nothing is claimed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = "hark21/gemini-task01/export-manifest/v1"


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load_package(path: str) -> dict:
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _within(child: str, parent: str) -> bool:
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def _git(repo: str, *args: str) -> bytes:
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True).stdout


def list_tree(repo: str, commit: str, prefix: str):
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit, "--", prefix)
    entries = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        meta, path = item.split(b"\t", 1)
        mode, kind, obj = meta.decode("ascii").split(" ")
        entries.append((mode, kind, obj, path.decode("utf-8")))
    return entries


def export(repo: str, out: str, manifest_path: str, package: dict) -> dict:
    commit = package["source_commit"]
    prefix = package["source_prefix"]
    resolved = _git(repo, "rev-parse", "--verify", commit + "^{commit}").decode("ascii").strip()
    if resolved != commit:
        raise SystemExit(f"commit {commit} resolves to {resolved}; refusing")
    if os.path.lexists(out) and (not os.path.isdir(out) or os.listdir(out)):
        raise SystemExit(f"--out {out!r} exists and is not an empty directory; refusing")
    if _within(manifest_path, out):
        raise SystemExit("--manifest must be outside --out")
    entries = list_tree(repo, commit, prefix)
    if not entries:
        raise SystemExit(f"no entries under {prefix!r} at {commit}")
    for mode, kind, obj, path in entries:
        if kind != "blob" or mode not in ("100644", "100755"):
            raise SystemExit(f"{path}: mode {mode} type {kind} is not a regular file; refusing")
        if not path.startswith(prefix) or ".." in path.split("/") or path.startswith("/"):
            raise SystemExit(f"{path}: unexpected path; refusing")
    os.makedirs(out, exist_ok=True)
    files = {}
    for mode, kind, obj, path in entries:
        data = _git(repo, "cat-file", "blob", obj)
        if git_blob_id(data) != obj:
            raise SystemExit(f"{path}: object bytes do not hash to {obj}")
        dest = os.path.join(out, *path.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "xb") as fh:
            fh.write(data)
        with open(dest, "rb") as fh:          # verify the bytes on disk, not in memory
            disk = fh.read()
        if git_blob_id(disk) != obj:
            raise SystemExit(f"{path}: bytes on disk differ from the object")
        files[path] = {"blob": obj, "size": len(disk), "sha256": hashlib.sha256(disk).hexdigest()}
    target = files.get(package["target_file"])
    if target is None or target["blob"] != package["target_blob"]:
        raise SystemExit(f"target {package['target_file']} is not blob {package['target_blob']}")
    manifest = {
        "schema": SCHEMA,
        "source_commit": commit,
        "source_prefix": prefix,
        "target_file": package["target_file"],
        "target_blob": package["target_blob"],
        "files": files,
        "note": "plain directory export of the fixed commit's src/; not a worktree, not a sandbox",
    }
    data = (json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    with open(manifest_path, "xb") as fh:
        fh.write(data)
    with open(manifest_path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    return {"manifest": manifest_path, "manifest_sha256": digest, "files": len(files), "out": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, help="a git repository that contains the fixed commit")
    ap.add_argument("--out", required=True, help="new, empty target directory")
    ap.add_argument("--manifest", required=True, help="manifest path, outside --out")
    ap.add_argument("--package", default=os.path.join(HERE, "package.json"))
    args = ap.parse_args(argv)
    try:
        result = export(args.repo, os.path.abspath(args.out), os.path.abspath(args.manifest),
                        load_package(args.package))
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
        print(f"export failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        print(f"export refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
