"""Source ID validation utilities.

Shared between engine and agent to avoid duplicating the placeholder-detection
regex and helper across multiple modules.
"""
from __future__ import annotations

import re

# Matches IDs that are almost certainly synthesised placeholders rather than
# real source references:
#   src_1, src_2, src_3, src_4  (and any src_<digits>)
#   source_1, source_2, ...
#   citation_1, citation_2, ...
#   ref_1, ref_2, ...
#   bare digits: "1", "2", "3"
_PLACEHOLDER_SOURCE_ID_RE = re.compile(
    r"^(?:src|source|citation|ref)_\d+$|^\d+$",
    re.IGNORECASE,
)


def looks_like_placeholder_source_id(source_id: str) -> bool:
    """Return True when *source_id* looks like a generated/fake placeholder.

    Rejected patterns:
      ``src_1``, ``src_2``, … ``src_N``
      ``source_1``, ``source_2``, …
      ``citation_1``, ``citation_2``, …
      ``ref_1``, ``ref_2``, …
      bare digit strings: ``"1"``, ``"2"``, ``"3"``, …
    """
    return bool(_PLACEHOLDER_SOURCE_ID_RE.match((source_id or "").strip()))


def has_only_placeholder_source_ids(source_ids: list) -> bool:
    """Return True when *source_ids* is non-empty and every entry is a placeholder."""
    return bool(source_ids) and all(
        looks_like_placeholder_source_id(str(s)) for s in source_ids
    )


def count_non_placeholder_source_ids(source_ids: list) -> int:
    """Return the number of source IDs that are **not** placeholders."""
    return sum(
        1 for s in (source_ids or [])
        if not looks_like_placeholder_source_id(str(s))
    )
