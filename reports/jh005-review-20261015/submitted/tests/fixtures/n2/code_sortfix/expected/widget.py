"""Fixture target: regional sales ranking widget (N2 code task expected state).

Reference fixed version: the `reverse=True` defect is removed, nothing else
changes. Used by checks and review; the real fix is produced by the model and
applied by the host in JH-006.
"""


def top_regions(records, n):
    """Return the top-n region names by sales desc, ties by region asc."""
    ranked = sorted(records, key=lambda r: (-r["sales"], r["region"]))
    return [r["region"] for r in ranked[:n]]
