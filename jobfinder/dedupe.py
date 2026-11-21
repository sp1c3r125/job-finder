import re

from .sources import PRIORITY


def norm_key(job):
    n = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    return f"{n(job.company)}|{n(job.title)}"


def dedupe(jobs):
    """Same company + title across sources -> keep the earliest posted (ties: PRIORITY order),
    list the other sources in also_seen_on."""
    groups = {}
    for j in jobs:
        groups.setdefault(norm_key(j), []).append(j)
    out = []
    for group in groups.values():
        group.sort(key=lambda j: (j.posted or "9999", PRIORITY.index(j.source)))
        keep = group[0]
        out.append(keep)
        for other in group[1:]:
            if other.source == keep.source:
                out.append(other)          # same source, e.g. one title posted for several locations: not a cross-source duplicate
            elif other.source not in keep.also_seen_on:
                keep.also_seen_on.append(other.source)
    return out
