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

Hark-派单-22 additions:
  * the package bytes must be the pinned approved package and the listed tree must
    be exactly the pinned src/ file set (pins.py), before anything is written;
  * --out must not be a link / junction (or under a component that is one): it is
    created fresh as a plain directory and checked with lstat before every write;
    files are created with O_CREAT|O_EXCL (and O_NOFOLLOW where the OS has it).

Exit codes: 0 exported and verified; 2 could not export (nothing is claimed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pins  # noqa: E402

SCHEMA = "hark21/gemini-task01/export-manifest/v1"


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load_package(path: str) -> dict:
    with open(path, "rb") as fh:
        data = fh.read()
    if hashlib.sha256(data).hexdigest() != pins.PACKAGE_SHA256:
        raise SystemExit("package bytes are not the pinned approved package; refusing")
    pkg = json.loads(data.decode("utf-8"))
    if not isinstance(pkg, dict):
        raise SystemExit("package is not a JSON object; refusing")
    return pkg


def _link_like(path: str) -> bool:
    st = os.lstat(path)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _plain_dir_chain(out: str) -> None:
    """Refuse when --out, or any existing component from its parent down, is a link /
    junction (checked with lstat, before anything is created or written)."""
    if os.path.lexists(out) and _link_like(out):
        raise SystemExit(f"--out {out!r} is a link or junction; refusing")
    parent = os.path.dirname(out)
    if os.path.lexists(parent) and _link_like(parent):
        raise SystemExit(f"parent of --out {parent!r} is a link or junction; refusing")


def _make_plain_dir(path: str) -> None:
    if not os.path.lexists(path):
        os.mkdir(path)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or (getattr(st, "st_file_attributes", 0) & 0x400) or not stat.S_ISDIR(st.st_mode):
        raise SystemExit(f"{path!r} is not a plain directory; refusing")


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
    if (package.get("source_commit"), package.get("source_prefix"), package.get("target_file"),
            package.get("target_blob")) != (pins.SOURCE_COMMIT, pins.SOURCE_PREFIX, pins.TARGET_FILE,
                                            pins.TARGET_BLOB):
        raise SystemExit("package does not pin the fixed commit/prefix/target/blob; refusing")
    commit = package["source_commit"]
    prefix = package["source_prefix"]
    resolved = _git(repo, "rev-parse", "--verify", commit + "^{commit}").decode("ascii").strip()
    if resolved != commit:
        raise SystemExit(f"commit {commit} resolves to {resolved}; refusing")
    _plain_dir_chain(out)
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
    listed = {path: obj for mode, kind, obj, path in entries}
    if listed != {p: v[0] for p, v in pins.SOURCE_FILES.items()}:
        raise SystemExit("the listed tree is not the pinned src/ file set; refusing")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    _make_plain_dir(out)
    files = {}
    for mode, kind, obj, path in entries:
        data = _git(repo, "cat-file", "blob", obj)
        if git_blob_id(data) != obj:
            raise SystemExit(f"{path}: object bytes do not hash to {obj}")
        parts = path.split("/")
        cur = out
        for part in parts[:-1]:
            cur = os.path.join(cur, part)
            _make_plain_dir(cur)
        dest = os.path.join(cur, parts[-1])
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(dest, flags, 0o644)
        with os.fdopen(fd, "wb") as fh:
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
