"""Tests for tools/normalize_existing_canonical_fields.py

Covers:
1. Dict field — only `value` updated, all metadata preserved.
2. Plain string field normalized.
3. Fuel type: Gasoline→petrol, Mild Hybrid Petrol→mild_hybrid, MHEV Petrol→mild_hybrid, PHEV→plug_in_hybrid.
4. Transmission: 8-speed automatic→automatic, DSG→dual_clutch, S-tronic→dual_clutch, Manual→manual.
5. Drivetrain: quattro→AWD, Quattro→AWD, e4WD→AWD, 4x4→4WD, 2WD NOT→FWD.
6. Dry-run: run_normalization does not write files.
7. Actual run: caller writes backup and persists changes.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.normalize import normalize_fuel_type, normalize_transmission, normalize_drivetrain
from tools.normalize_existing_canonical_fields import (
    normalize_field,
    normalize_drivetrain_field,
    run_normalization,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vf(value, **meta) -> dict:
    """Build a minimal VerifiedField dict."""
    defaults = {
        "value": value,
        "status": "verified",
        "confidence": "high",
        "sources_count": 2,
        "source_ids": ["src_a", "src_b"],
        "used_in_compare": True,
        "reason": "verified from 2 source(s)",
    }
    defaults.update(meta)
    return defaults


def _minimal_canonical(variants: list[dict]) -> dict:
    return {
        "accumulated_clean_export": {"variants": variants},
        "batch_state": {
            "processed_seed_ids": [],
            "needs_retry_seed_ids": [],
        },
    }


# ---------------------------------------------------------------------------
# 1. Dict field — metadata preserved, only `value` changed
# ---------------------------------------------------------------------------

class TestDictFieldMetadataPreserved:
    def test_value_updated(self):
        field = _vf("Petrol")
        new_field, changed, old, new = normalize_field(field, normalize_fuel_type)
        assert changed
        assert isinstance(new_field, dict)
        assert new_field["value"] == "petrol"

    def test_status_preserved(self):
        field = _vf("Petrol")
        new_field, _, _, _ = normalize_field(field, normalize_fuel_type)
        assert new_field["status"] == "verified"

    def test_confidence_preserved(self):
        field = _vf("Petrol")
        new_field, _, _, _ = normalize_field(field, normalize_fuel_type)
        assert new_field["confidence"] == "high"

    def test_source_ids_preserved(self):
        field = _vf("Petrol")
        new_field, _, _, _ = normalize_field(field, normalize_fuel_type)
        assert new_field["source_ids"] == ["src_a", "src_b"]

    def test_sources_count_preserved(self):
        field = _vf("Petrol")
        new_field, _, _, _ = normalize_field(field, normalize_fuel_type)
        assert new_field["sources_count"] == 2

    def test_used_in_compare_preserved(self):
        field = _vf("Petrol")
        new_field, _, _, _ = normalize_field(field, normalize_fuel_type)
        assert new_field["used_in_compare"] is True

    def test_reason_preserved(self):
        field = _vf("Petrol")
        new_field, _, _, _ = normalize_field(field, normalize_fuel_type)
        assert new_field["reason"] == "verified from 2 source(s)"

    def test_already_normalized_no_change(self):
        field = _vf("petrol")
        _, changed, _, _ = normalize_field(field, normalize_fuel_type)
        assert not changed

    def test_none_value_no_change(self):
        field = _vf(None)
        _, changed, _, _ = normalize_field(field, normalize_fuel_type)
        assert not changed

    def test_empty_string_no_change(self):
        field = _vf("")
        _, changed, _, _ = normalize_field(field, normalize_fuel_type)
        assert not changed


# ---------------------------------------------------------------------------
# 2. Plain string field
# ---------------------------------------------------------------------------

class TestPlainStringField:
    def test_plain_string_normalized(self):
        new_field, changed, old, new = normalize_field("Gasoline", normalize_fuel_type)
        assert changed
        assert new_field == "petrol"
        assert old == "Gasoline"
        assert new == "petrol"

    def test_plain_already_normalized_no_change(self):
        _, changed, _, _ = normalize_field("petrol", normalize_fuel_type)
        assert not changed

    def test_none_plain_no_change(self):
        _, changed, _, _ = normalize_field(None, normalize_fuel_type)
        assert not changed


# ---------------------------------------------------------------------------
# 3. Fuel type normalization
# ---------------------------------------------------------------------------

class TestFuelTypeNormalization:
    """All cases via the dict wrapper to confirm value-only update."""

    def _norm(self, val: str) -> str:
        field = _vf(val, status="unverified", confidence="low", sources_count=0,
                    source_ids=[], used_in_compare=False, reason=None)
        new_field, changed, _, new_val = normalize_field(field, normalize_fuel_type)
        return new_val if changed else val

    def test_gasoline_to_petrol(self):
        assert self._norm("Gasoline") == "petrol"

    def test_petrol_upper_to_petrol(self):
        assert self._norm("Petrol") == "petrol"

    def test_mild_hybrid_petrol_to_mild_hybrid(self):
        assert self._norm("Mild Hybrid Petrol") == "mild_hybrid"

    def test_petrol_mild_hybrid_to_mild_hybrid(self):
        assert self._norm("Petrol Mild Hybrid") == "mild_hybrid"

    def test_mhev_petrol_to_mild_hybrid(self):
        assert self._norm("MHEV Petrol") == "mild_hybrid"

    def test_phev_to_plug_in_hybrid(self):
        assert self._norm("PHEV") == "plug_in_hybrid"

    def test_plug_in_hybrid_label(self):
        assert self._norm("Plug-in Hybrid") == "plug_in_hybrid"

    def test_electric_to_electric(self):
        assert self._norm("Electric") == "electric"

    def test_ev_to_electric(self):
        assert self._norm("EV") == "electric"

    def test_hybrid_to_hybrid(self):
        assert self._norm("Hybrid") == "hybrid"

    def test_diesel_to_diesel(self):
        assert self._norm("Diesel") == "diesel"


# ---------------------------------------------------------------------------
# 4. Transmission normalization
# ---------------------------------------------------------------------------

class TestTransmissionNormalization:
    def _norm(self, val: str) -> str:
        field = _vf(val, status="unverified", confidence="low", sources_count=0,
                    source_ids=[], used_in_compare=False, reason=None)
        new_field, changed, _, new_val = normalize_field(field, normalize_transmission)
        return new_val if changed else val

    def test_8speed_automatic_lower(self):
        assert self._norm("8-speed automatic") == "automatic"

    def test_8speed_automatic_mixed(self):
        assert self._norm("8-Speed Automatic") == "automatic"

    def test_automatic_upper(self):
        assert self._norm("Automatic") == "automatic"

    def test_dsg_to_dual_clutch(self):
        assert self._norm("DSG") == "dual_clutch"

    def test_7speed_dsg_to_dual_clutch(self):
        assert self._norm("7-speed DSG") == "dual_clutch"

    def test_s_tronic_hyphen_to_dual_clutch(self):
        assert self._norm("S-tronic") == "dual_clutch"

    def test_s_tronic_space_to_dual_clutch(self):
        assert self._norm("7-speed S tronic") == "dual_clutch"

    def test_s_tronic_upper_to_dual_clutch(self):
        assert self._norm("7-speed S-Tronic") == "dual_clutch"

    def test_manual_upper(self):
        assert self._norm("Manual") == "manual"

    def test_6speed_manual(self):
        assert self._norm("6-speed manual") == "manual"

    def test_single_speed_ev(self):
        assert self._norm("Single-speed EV") == "single_speed_ev"


# ---------------------------------------------------------------------------
# 5. Drivetrain normalization
# ---------------------------------------------------------------------------

class TestDrivetrainNormalization:
    def _norm(self, val: str) -> str:
        field = _vf(val, status="unverified", confidence="low", sources_count=0,
                    source_ids=[], used_in_compare=False, reason=None)
        new_field, changed, old_val, new_val = normalize_drivetrain_field(field)
        if not changed:
            return old_val
        return new_field.get("value") if isinstance(new_field, dict) else str(new_field)

    def test_quattro_lower_to_awd(self):
        assert self._norm("quattro") == "AWD"

    def test_quattro_title_to_awd(self):
        assert self._norm("Quattro") == "AWD"

    def test_e4wd_to_awd(self):
        assert self._norm("e4WD") == "AWD"

    def test_xdrive_to_awd(self):
        assert self._norm("xDrive") == "AWD"

    def test_4motion_to_awd(self):
        assert self._norm("4Motion") == "AWD"

    def test_4matic_to_awd(self):
        assert self._norm("4MATIC") == "AWD"

    def test_e_awd_to_awd(self):
        assert self._norm("e-AWD") == "AWD"

    def test_4x4_lower_to_4wd(self):
        assert self._norm("4x4") == "4WD"

    def test_4x4_upper_to_4wd(self):
        assert self._norm("4X4") == "4WD"

    def test_fwd_no_change(self):
        assert self._norm("FWD") == "FWD"

    def test_rwd_no_change(self):
        assert self._norm("RWD") == "RWD"

    def test_awd_no_change(self):
        assert self._norm("AWD") == "AWD"

    def test_2wd_not_converted_to_fwd(self):
        """2WD must NOT be silently mapped to FWD."""
        result = self._norm("2WD")
        assert result != "FWD", f"2WD was incorrectly mapped to FWD (got {result!r})"


# ---------------------------------------------------------------------------
# 6. run_normalization does not write files (dry-run behaviour)
# ---------------------------------------------------------------------------

class TestRunNormalizationNoWrite:
    def test_no_file_written(self, tmp_path):
        canon = _minimal_canonical([
            {
                "variant_id": "test__v1",
                "make": "BMW",
                "model": "3 Series",
                "fuel_type": _vf("Petrol"),
                "transmission": _vf("Automatic"),
                "drivetrain": _vf("quattro"),
            }
        ])
        canon_path = tmp_path / "canonical.json"
        canon_path.write_text(json.dumps(canon), encoding="utf-8")
        mtime_before = canon_path.stat().st_mtime
        time.sleep(0.05)

        # run_normalization only modifies in-memory data
        data = json.loads(canon_path.read_text(encoding="utf-8"))
        run_normalization(data)

        # File must be untouched
        assert canon_path.stat().st_mtime == mtime_before

    def test_stats_returned(self):
        canon = _minimal_canonical([
            {
                "variant_id": "test__v1",
                "make": "BMW",
                "model": "3 Series",
                "fuel_type": _vf("Petrol"),
                "transmission": _vf("Automatic"),
                "drivetrain": _vf("quattro"),
            }
        ])
        _, stats = run_normalization(canon)
        assert stats["variants_scanned"] == 1
        assert stats["fuel_type_total_changed"] == 1     # Petrol → petrol
        assert stats["transmission_total_changed"] == 1  # Automatic → automatic
        assert stats["drivetrain_total_changed"] == 1    # quattro → AWD


# ---------------------------------------------------------------------------
# 7. Actual run — caller writes backup and changes are persisted
# ---------------------------------------------------------------------------

class TestActualRunPersist:
    def test_backup_created(self, tmp_path):
        from datetime import datetime, timezone

        canon = _minimal_canonical([
            {
                "variant_id": "test__v1",
                "make": "BMW",
                "model": "3 Series",
                "fuel_type": _vf("Petrol"),
                "transmission": _vf("8-speed automatic"),
                "drivetrain": _vf("quattro"),
            }
        ])
        canon_path = tmp_path / "resume_package_canonical.json"
        canon_path.write_text(json.dumps(canon), encoding="utf-8")

        data = json.loads(canon_path.read_text(encoding="utf-8"))
        data, _ = run_normalization(data)

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = canon_path.with_name(f"resume_package_canonical_backup_{ts}.json")
        shutil.copy2(canon_path, backup)
        canon_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        assert backup.exists(), "backup file was not created"

    def test_changes_persisted(self, tmp_path):
        canon = _minimal_canonical([
            {
                "variant_id": "test__v1",
                "make": "BMW",
                "model": "3 Series",
                "fuel_type": _vf("Petrol", status="verified", confidence="high",
                                  sources_count=2, source_ids=["s1", "s2"],
                                  used_in_compare=True, reason="verified from 2 source(s)"),
                "transmission": _vf("8-speed Automatic"),
                "drivetrain": _vf("quattro"),
            }
        ])
        canon_path = tmp_path / "resume_package_canonical.json"
        canon_path.write_text(json.dumps(canon), encoding="utf-8")

        data = json.loads(canon_path.read_text(encoding="utf-8"))
        data, _ = run_normalization(data)
        canon_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        reloaded = json.loads(canon_path.read_text(encoding="utf-8"))
        v = reloaded["accumulated_clean_export"]["variants"][0]

        assert v["fuel_type"]["value"] == "petrol"
        assert v["transmission"]["value"] == "automatic"
        assert v["drivetrain"]["value"] == "AWD"

        # Metadata must be preserved
        assert v["fuel_type"]["status"] == "verified"
        assert v["fuel_type"]["confidence"] == "high"
        assert v["fuel_type"]["source_ids"] == ["s1", "s2"]
        assert v["fuel_type"]["sources_count"] == 2
        assert v["fuel_type"]["used_in_compare"] is True
