"""
JH-Agent OS Artifact Store v1.0.0 (Phase 3)
In-memory/SQLite artifact lineage with control-owned SHA-256 and versions.
Memory artifacts use virtual paths of the form: mem://<task_id>/<artifact_id>
They are NEVER written to the real filesystem; snapshotting is read-only.
"""

import hashlib
from typing import Any, Dict, List, Optional


def content_sha256(content: str) -> str:
    """Authoritative content hash computed by the control layer, never by the model."""
    if not isinstance(content, str):
        raise ValueError("Artifact content must be a string")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def mem_artifact_path(task_id: str, artifact_id: str) -> str:
    return f"mem://{task_id}/{artifact_id}"


def parse_mem_artifact_path(path: str) -> tuple:
    if not isinstance(path, str) or not path.startswith("mem://"):
        raise ValueError(f"Not a memory artifact path: {path!r}")
    rest = path[len("mem://"):]
    task_id, _, artifact_id = rest.partition("/")
    if not task_id or not artifact_id:
        raise ValueError(f"Malformed memory artifact path: {path!r}")
    return task_id, artifact_id


def is_mem_artifact_path(path: str) -> bool:
    return isinstance(path, str) and path.startswith("mem://")


class ArtifactStore:
    """Read-only snapshot view over persisted artifact versions.

    Exposes snapshot_artifacts(paths) so control_guards.artifact_snapshot can
    treat memory artifacts exactly like tool-produced artifacts: it returns the
    CURRENT content for every requested path and lets the control layer hash it.
    """

    def __init__(self, storage):
        self.storage = storage

    def save_version(
        self,
        task_id: str,
        *,
        envelope: Dict[str, Any],
        version: int,
        generation_attempt_id: Optional[str],
    ) -> Dict[str, Any]:
        sha256 = content_sha256(envelope["content"])
        record = self.storage.save_artifact_version(
            task_id,
            version=version,
            envelope=envelope,
            sha256=sha256,
            generation_attempt_id=generation_attempt_id,
        )
        return record

    def get_latest(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.storage.get_latest_artifact(task_id)

    def list_versions(self, task_id: str) -> List[Dict[str, Any]]:
        return self.storage.list_artifact_versions(task_id)

    def evidence_ref(self, task_id: str) -> Optional[str]:
        latest = self.get_latest(task_id)
        if not latest:
            return None
        return f"artifact:{latest['artifact_id']}@v{latest['version']}:sha256={latest['sha256']}"

    def snapshot_artifacts(self, paths: List[str]) -> Dict[str, str]:
        """Return current content for every requested memory artifact path."""
        contents: Dict[str, str] = {}
        for path in paths:
            task_id, _artifact_id = parse_mem_artifact_path(path)
            latest = self.storage.get_latest_artifact(task_id)
            if not latest:
                raise KeyError(f"No artifact version persisted for '{path}'")
            contents[path] = latest["content"]
        return contents
