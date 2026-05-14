"""Canonical store: single source of truth for engine state.

Provides load / validate / atomic save / backup. NEVER writes any state outside
``data/canonical/resume_package_canonical.json``.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CANONICAL_PATH = Path("data/canonical/resume_package_canonical.json")
BACKUP_PATH = Path("data/canonical/resume_package_backup_previous.json")


class CanonicalError(RuntimeError):
    pass


def canonical_path() -> Path:
    """Return the canonical file path (overridable via env for tests)."""
    override = os.environ.get("CANONICAL_PATH")
    if override:
        return Path(override)
    return CANONICAL_PATH


def backup_path() -> Path:
    override = os.environ.get("CANONICAL_BACKUP_PATH")
    if override:
        return Path(override)
    return BACKUP_PATH


def load_canonical(path: Path | None = None) -> dict:
    p = Path(path) if path else canonical_path()
    if not p.exists():
        raise CanonicalError(f"canonical missing at {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CanonicalError(f"canonical is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CanonicalError("canonical root is not a JSON object")
    return data


def refresh_canonical_counts(canonical: dict) -> dict:
    """Recompute all counts from actual data in canonical.

    Mutates the ``counts`` section of *canonical* in place and returns it.
    Never trusts old counts — always recalculates from actual arrays/lists.
    """
    ace = canonical.get("accumulated_clean_export") or {}
    variants = ace.get("variants") or []
    bs = canonical.get("batch_state") or {}

    counts = canonical.setdefault("counts", {})
    total_variants = len(variants)
    counts["total_variants"] = total_variants
    counts["variants"] = total_variants  # legacy alias — kept in sync until migrated away
    counts["processed_seeds"] = len(bs.get("processed_seed_ids") or [])
    counts["needs_retry_seeds"] = len(bs.get("needs_retry_seed_ids") or [])
    if bs.get("total_seeds") is not None:
        counts["total_seeds"] = bs["total_seeds"]
    counts["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    return canonical


def validate_canonical(data: dict) -> tuple[bool, list[str]]:
    """Return (ok, errors). Used both before and after save."""
    errs: list[str] = []
    if not isinstance(data, dict):
        return False, ["root_not_dict"]

    ace = data.get("accumulated_clean_export")
    if not isinstance(ace, dict):
        errs.append("missing_accumulated_clean_export")
        variants: list = []
    else:
        variants = ace.get("variants")
        if not isinstance(variants, list):
            errs.append("variants_not_a_list")
            variants = []

    bs = data.get("batch_state")
    if not isinstance(bs, dict):
        errs.append("missing_batch_state")
    else:
        for key in ("processed_seed_ids", "needs_retry_seed_ids"):
            if not isinstance(bs.get(key), list):
                errs.append(f"missing_{key}")

    # count consistency check
    counts = data.get("counts")
    if isinstance(counts, dict):
        stored_total = counts.get("total_variants")
        actual_total = len(variants)
        if stored_total is not None and stored_total != actual_total:
            errs.append(
                f"counts_total_variants_mismatch: "
                f"stored={stored_total} actual={actual_total}"
            )
        if isinstance(bs, dict):
            stored_processed = counts.get("processed_seeds")
            actual_processed = len(bs.get("processed_seed_ids") or [])
            if stored_processed is not None and stored_processed != actual_processed:
                errs.append(
                    f"counts_processed_seeds_mismatch: "
                    f"stored={stored_processed} actual={actual_processed}"
                )
            stored_retry = counts.get("needs_retry_seeds")
            actual_retry = len(bs.get("needs_retry_seed_ids") or [])
            if stored_retry is not None and stored_retry != actual_retry:
                errs.append(
                    f"counts_needs_retry_seeds_mismatch: "
                    f"stored={stored_retry} actual={actual_retry}"
                )

    # duplicate variant_id check
    seen: set[str] = set()
    duplicates = 0
    for v in variants:
        if not isinstance(v, dict):
            continue
        vid = str(v.get("variant_id") or "").strip()
        if not vid:
            continue
        if vid in seen:
            duplicates += 1
        else:
            seen.add(vid)
    if duplicates > 0:
        errs.append(f"duplicate_variant_id:{duplicates}")

    # forbidden stale token "s1"
    if isinstance(bs, dict):
        text = json.dumps(bs, ensure_ascii=False)
        if '"s1"' in text:
            errs.append("forbidden_s1_token_in_batch_state")

    return (len(errs) == 0), errs


def backup_canonical_before_write(path: Path | None = None) -> Path | None:
    p = Path(path) if path else canonical_path()
    if not p.exists():
        return None
    bkp = backup_path() if path is None else Path(path).with_name(Path(path).stem + "_backup_previous.json")
    bkp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, bkp)
    return bkp


def save_canonical_atomic(data: dict, path: Path | None = None) -> dict:
    """Write canonical atomically: temp file -> validate -> rename -> revalidate.

    Returns a result dict with keys: ok, path, error.
    """
    p = Path(path) if path else canonical_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    # Always refresh counts from actual data before validating or writing.
    refresh_canonical_counts(data)

    ok, errs = validate_canonical(data)
    if not ok:
        return {"ok": False, "path": str(p), "error": f"pre-save validation failed: {errs}"}

    backup_canonical_before_write(p)

    fd, tmp_path = tempfile.mkstemp(prefix=".canonical_", suffix=".json", dir=str(p.parent))
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

        # validate the temp file by reloading
        try:
            reloaded = json.loads(tmp.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "path": str(p), "error": f"temp file unreadable: {exc}"}
        ok2, errs2 = validate_canonical(reloaded)
        if not ok2:
            return {"ok": False, "path": str(p), "error": f"temp file validation failed: {errs2}"}

        os.replace(tmp_path, p)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    # final revalidate after rename
    final = json.loads(p.read_text(encoding="utf-8"))
    ok3, errs3 = validate_canonical(final)
    if not ok3:
        return {"ok": False, "path": str(p), "error": f"post-save validation failed: {errs3}"}
    return {"ok": True, "path": str(p), "error": None}


def write_canonical(data: dict, path: Path | None = None) -> dict:
    """Public alias for save_canonical_atomic."""
    return save_canonical_atomic(data, path=path)
