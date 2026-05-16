from __future__ import annotations

from agent.prompts import build_discovery_prompt, build_retry_discovery_prompt


SEED_HAVAL_H6 = {
    "make": "Haval",
    "model": "H6",
    "year_start": 2022,
    "year_end": 2026,
    "market": "IL",
}

GOOD_IL_NAME_EXAMPLE = """GOOD:
   {
     "global_model_name": "Daewoo Lacetti",
     "official_marketed_name_il": "Chevrolet Optra",
     "market_scope": "IL-confirmed","""


def test_build_discovery_prompt_haval_h6_il():
    prompt = build_discovery_prompt(SEED_HAVAL_H6, market="IL")

    assert isinstance(prompt, str)
    assert prompt
    assert "Haval" in prompt
    assert "H6" in prompt
    assert "candidate_variants" in prompt
    assert GOOD_IL_NAME_EXAMPLE in prompt
    assert "BAD:\n   {" in prompt
    assert "}" in prompt


def test_discovery_prompt_no_placeholder_source_id_rule():
    """Prompt must instruct the model never to use placeholder source IDs."""
    prompt = build_discovery_prompt(SEED_HAVAL_H6, market="IL")
    assert "placeholder source IDs" in prompt
    assert "src_1" in prompt          # referenced in the rule as a forbidden example
    assert "no_variants_source_ids" in prompt
    assert "no_reliable_sources_found" in prompt


def test_build_retry_discovery_prompt_haval_h6_il():
    prompt = build_retry_discovery_prompt(SEED_HAVAL_H6, market="IL")

    assert isinstance(prompt, str)
    assert prompt
    assert "Haval" in prompt
    assert "H6" in prompt
    assert "candidate_variants" in prompt
    assert "IMPORTANT — RETRY ATTEMPT" in prompt
    assert GOOD_IL_NAME_EXAMPLE in prompt
    assert "BAD:\n   {" in prompt
    assert "}" in prompt
