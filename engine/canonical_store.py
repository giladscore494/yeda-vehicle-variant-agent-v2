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


def repair_canonical_buckets(canonical: dict) -> dict:
    """Rebuild top-level verified_variants and partial_variants from accumulated_clean_export.variants.

    Splits variants by verification_status:
    - ``"verified"`` → ``verified_variants``
    - everything else → ``partial_variants``

    Ensures ``len(verified_variants) + len(partial_variants) == len(accumulated_clean_export.variants)``.
    Mutates *canonical* in place and returns it.
    """
    ace = canonical.get("accumulated_clean_export") or {}
    variants = ace.get("variants") or []
    verified: list[dict] = []
    partial: list[dict] = []
    for v in variants:
        if not isinstance(v, dict):
            continue
        if v.get("verification_status") == "verified":
            verified.append(v)
        else:
            partial.append(v)
    canonical["verified_variants"] = verified
    canonical["partial_variants"] = partial
    return canonical


def refresh_canonical_counts(canonical: dict) -> dict:
    """Recompute all counts from actual data in canonical.

    Mutates the ``counts`` section of *canonical* in place and returns it.
    Never trusts old counts — always recalculates from actual arrays/lists.
    Also syncs batch_state.active_mode and batch_state.processed_seeds from
    their authoritative sources so stale values cannot persist.
    """
    ace = canonical.get("accumulated_clean_export") or {}
    variants = ace.get("variants") or []
    bs = canonical.get("batch_state") or {}

    # Compute verified/partial split and unique make/model counts from variants.
    verified_count = 0
    partial_count = 0
    makes: set[str] = set()
    models: set[tuple[str, str]] = set()
    for v in variants:
        if not isinstance(v, dict):
            continue
        if v.get("verification_status") == "verified":
            verified_count += 1
        else:
            partial_count += 1
        mk = (v.get("make") or "").strip()
        mo = (v.get("model") or "").strip()
        if mk:
            makes.add(mk)
        models.add((mk, mo))

    processed_ids = list(bs.get("processed_seed_ids") or [])
    needs_retry_ids = list(bs.get("needs_retry_seed_ids") or [])

    counts = canonical.setdefault("counts", {})
    total_variants = len(variants)
    counts["total_variants"] = total_variants
    counts["variants"] = total_variants  # legacy alias — kept in sync until migrated away
    counts["verified"] = verified_count
    counts["partial"] = partial_count
    counts["makes_count"] = len(makes)
    counts["models_count"] = len(models)
    counts["processed_seeds"] = len(processed_ids)
    counts["needs_retry_seeds"] = len(needs_retry_ids)
    if bs.get("total_seeds") is not None:
        counts["total_seeds"] = bs["total_seeds"]
    counts["unresolved"] = len(bs.get("failed_seed_ids") or [])
    counts["last_updated_at"] = datetime.now(timezone.utc).isoformat()

    # Fix active_mode: must reflect needs_retry_seed_ids, not stale stored value.
    if isinstance(canonical.get("batch_state"), dict):
        canonical["batch_state"]["active_mode"] = (
            "normal_batch" if not needs_retry_ids else "rerun_queue_required"
        )
        # Sync processed_seeds list from processed_seed_ids (canonical source of truth).
        canonical["batch_state"]["processed_seeds"] = processed_ids

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

    # Compute actual verified/partial from variants for count consistency checks.
    actual_verified = sum(
        1 for v in variants if isinstance(v, dict) and v.get("verification_status") == "verified"
    )
    actual_partial = len(variants) - actual_verified

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
        stored_verified = counts.get("verified")
        if stored_verified is not None and stored_verified != actual_verified:
            errs.append(
                f"counts_verified_mismatch: "
                f"stored={stored_verified} actual={actual_verified}"
            )
        stored_partial = counts.get("partial")
        if stored_partial is not None and stored_partial != actual_partial:
            errs.append(
                f"counts_partial_mismatch: "
                f"stored={stored_partial} actual={actual_partial}"
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

    # Always refresh counts and top-level buckets from actual data before validating or writing.
    refresh_canonical_counts(data)
    repair_canonical_buckets(data)

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
