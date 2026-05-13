"""Minimal Streamlit UI for the clean vehicle-variant engine.

Tabs:
1. Main Run
2. Manual Single Model
3. Export / Diagnostics

No repair buttons. No recover buttons. No legacy diagnostics.
The canonical file is the only state.
"""
from __future__ import annotations

import json
from typing import Any

import streamlit as st

from agent.runner import run_seed
from core.final_export_builder import build_final_export, export_as_json_bytes
from engine import queue
from engine.canonical_store import load_canonical, validate_canonical, canonical_path
from engine.merge_variants import merge_variants
from engine.run_next import MAX_BATCH_SIZE, run_next_model, run_selected_seed


st.set_page_config(page_title="Vehicle Variant Engine v2", layout="wide")


def _load() -> dict | None:
    try:
        return load_canonical()
    except Exception as exc:
        st.error(f"Failed to load canonical: {exc}")
        return None


def _main_run_tab(canonical: dict) -> None:
    summary = queue.summarize_state(canonical)
    cols = st.columns(4)
    cols[0].metric("Mode", summary["mode"])
    cols[1].metric("Variants", summary["variants_count"])
    cols[2].metric("Processed", summary["processed_seed_count"])
    cols[3].metric("Needs retry", summary["needs_retry_count"])
    st.write(f"**Selected next seed:** `{summary['selected_seed_id']}`")
    st.write(f"**Normal next_seed_id:** `{summary['next_seed_id']}`")
    st.write(f"**Normal last_completed_seed_id:** `{summary['last_completed_seed_id']}`")

    if summary["mode"] == "problem_queue":
        prog = summary["problem_queue_progress"]
        st.subheader("Problem queue progress")
        c = st.columns(4)
        c[0].metric("Total", prog["total"])
        c[1].metric("Completed", prog["completed"])
        c[2].metric("Pending", prog["pending"])
        c[3].metric("Position", prog["current_position"])
        st.progress(min(1.0, max(0.0, prog["percent"])))
        st.caption(f"Normal paused at: {prog['normal_paused_at']}")

    st.divider()
    batch_size = st.number_input("Batch size", min_value=1, max_value=MAX_BATCH_SIZE, value=1, step=1)
    st.warning("Runs stop automatically on first save/push/validation failure.")
    if st.button("Run next model", type="primary"):
        with st.spinner("Running next model..."):
            result = run_next_model(batch_size=int(batch_size))
        processed = result.get("processed_count", 0)
        if result.get("ok"):
            st.success(f"Batch completed: processed {processed} seed(s).")
        else:
            st.error(f"Run stopped: {result.get('stop_reason') or 'unknown error'}")
            if result.get("stop_reason") and "ahead of GitHub" in result["stop_reason"]:
                st.warning(result["stop_reason"])
        st.json(result)


def _manual_tab() -> None:
    st.subheader("Manual single model")
    cols = st.columns(5)
    make = cols[0].text_input("make", value="")
    model = cols[1].text_input("model", value="")
    year_start = cols[2].number_input("year_start", min_value=1900, max_value=2100, value=2020)
    year_end = cols[3].number_input("year_end", min_value=1900, max_value=2100, value=2026)
    market = cols[4].text_input("market", value="IL")

    norm = lambda v: "_".join(str(v or "").strip().lower().split()).replace("/", "-")
    seed_id = f"{norm(make)}__{norm(model)}__{int(year_start)}__{int(year_end)}__{norm(market)}"
    st.code(seed_id)

    if "manual_variants" not in st.session_state:
        st.session_state["manual_variants"] = []

    if st.button("Run single model"):
        with st.spinner("Discovering..."):
            result = run_seed(seed_id)
        st.session_state["manual_variants"] = result.get("variants") or []
        if result.get("ok"):
            st.success(f"Discovery ok, {len(result.get('variants') or [])} candidates")
        else:
            st.error(f"Discovery failed: {result.get('error')}")
        st.json({k: v for k, v in result.items() if k != "discovery"})

    variants = st.session_state.get("manual_variants") or []
    if variants:
        st.subheader("Preview")
        st.json(variants)
        if st.button("Merge into canonical"):
            with st.spinner("Running through engine..."):
                push_result = run_selected_seed(seed_id, push_fn=None)
            if push_result.get("ok"):
                st.success("Merged and saved canonical.")
            else:
                st.error(f"Save failed: {push_result.get('error')}")
            st.json(push_result)


def _diagnostics_tab(canonical: dict) -> None:
    st.subheader("Validation")
    ok, errs = validate_canonical(canonical)
    if ok:
        st.success("Canonical is valid.")
    else:
        st.error(f"Validation issues: {errs}")

    variants = (canonical.get("accumulated_clean_export") or {}).get("variants") or []
    vids: list[str] = [str(v.get("variant_id") or "") for v in variants]
    duplicates = len(vids) - len(set(vids))

    bs = canonical.get("batch_state") or {}
    cols = st.columns(4)
    cols[0].metric("Variants", len(variants))
    cols[1].metric("Duplicate variant_id", duplicates)
    cols[2].metric("Needs retry", len(bs.get("needs_retry_seed_ids") or []))
    cols[3].metric("Processed", len(bs.get("processed_seed_ids") or []))

    st.caption(f"Canonical path: `{canonical_path()}`")
    st.write(f"**Selected next seed:** `{queue.select_next_seed(canonical)['selected_seed_id']}`")

    st.subheader("Needs retry seeds")
    st.write(bs.get("needs_retry_seed_ids") or [])

    st.subheader("Download")
    payload = json.dumps(canonical, ensure_ascii=False, indent=2).encode("utf-8")
    st.download_button(
        "Download canonical",
        data=payload,
        file_name="resume_package_canonical.json",
        mime="application/json",
    )
    st.download_button(
        "Download clean final export",
        data=export_as_json_bytes(variants),
        file_name="final_export.json",
        mime="application/json",
    )


def main() -> None:
    st.title("Yeda Vehicle Variant Agent v2")
    canonical = _load()
    if canonical is None:
        return
    tab_main, tab_manual, tab_diag = st.tabs(["Main Run", "Manual Single Model", "Export / Diagnostics"])
    with tab_main:
        _main_run_tab(canonical)
    with tab_manual:
        _manual_tab()
    with tab_diag:
        _diagnostics_tab(canonical)


if __name__ == "__main__":
    main()
