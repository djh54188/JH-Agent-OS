"""
JH-Agent OS Minimal Memory Writer v1.0.0 (Phase 3)
Writes memory_record.v1.json entries at task terminal states:
- succeeded: one verified task-scoped fact with source, plus at most one
  unverified generalization derived from the single result.
- failed: one unverified negative_result keeping the failure evidence.
No retrieval platform, automatic learning, or self-optimization is built here.
"""

import json
from typing import Any, Dict
import uuid

from src.storage import TaskStorage


def _base(record_task_id: str, category: str, verification_status: str, title: str, content: str,
          key: str, source: str, tags=None) -> Dict[str, Any]:
    # created_at / updated_at are filled by storage.record_memory before validation.
    return {
        "schema_version": "1.0.0",
        "memory_id": f"mem_{uuid.uuid4().hex[:12]}",
        "task_id": record_task_id,
        "category": category,
        "title": title[:256],
        "key": key,
        "content": content,
        "tags": tags or [],
        "confidence": 0.9 if verification_status == "verified" else 0.5,
        "verification_status": verification_status,
        "source_event": source,
    }


def record_task_outcome_memory(storage: TaskStorage, task: Dict[str, Any]) -> list:
    """Persist minimal outcome memories. Caller must pass a TERMINAL task (succeeded/failed)."""
    task_id = task["task_id"]
    status = task["status"]
    created = []

    if status == "succeeded":
        latest = storage.get_latest_artifact(task_id)
        artifact_source = (
            f"artifact:{latest['artifact_id']}@v{latest['version']}:sha256={latest['sha256']}"
            if latest else "no_artifact"
        )
        # Verified task-scoped fact: what was produced and how it was accepted.
        fact = _base(
            task_id, "domain_constraint", "verified",
            f"Task {task_id} produced an accepted artifact",
            f"Task '{task['title']}' reached succeeded with acceptance criteria met. "
            f"Artifact source: {artifact_source}. Independent review verdict: {task.get('review_verdict')}.",
            key=f"tasks.{task_id}.outcome",
            source=f"task:{task_id};{artifact_source};review_verdict={task.get('review_verdict')}",
            tags=["phase3", "outcome"],
        )
        storage.record_memory(fact)
        created.append(fact["memory_id"])

        # Generalization derived from a single result: stays unverified by rule.
        generalization = _base(
            task_id, "successful_pattern", "unverified",
            f"Pattern candidate from single success of task {task_id}",
            f"Single-task observation only: the generation->acceptance->independent-review loop "
            f"completed for '{task['title']}'. This is NOT a verified success rate; do not cite as a statistic.",
            key=f"tasks.{task_id}.pattern_candidate",
            source=f"task:{task_id};single_result",
            tags=["phase3", "candidate"],
        )
        storage.record_memory(generalization)
        created.append(generalization["memory_id"])

    elif status == "failed":
        latest = storage.get_latest_artifact(task_id)
        artifact_note = (
            f" Last artifact: v{latest['version']} sha256={latest['sha256']}." if latest else ""
        )
        negative = _base(
            task_id, "negative_result", "unverified",
            f"Task {task_id} failed",
            f"Task '{task['title']}' ended failed. Failure evidence: {task.get('error_info')}.{artifact_note} "
            f"Kept as candidate experience; not a verified rule.",
            key=f"tasks.{task_id}.failure",
            source=f"task:{task_id};error_info",
            tags=["phase3", "negative_result"],
        )
        storage.record_memory(negative)
        created.append(negative["memory_id"])

    # cancelled / needs_attention: no memory record in the minimal Phase 3 scope.
    return created
