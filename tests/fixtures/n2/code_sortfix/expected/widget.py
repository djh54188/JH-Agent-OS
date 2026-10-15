"""Fixture target: regional sales ranking widget (N2 code task baseline).

Baseline contains exactly one defect: `reverse=True` inverts the ranking order.
See task.json for the required behavior table. Synthetic fixture only; never a
user file.
"""


def top_regions(records, n):
    """Return the top-n region names by sales desc, ties by region asc."""
    ranked = sorted(records, key=lambda r: (-r["sales"], r["region"]))
    return [r["region"] for r in ranked[:n]]
