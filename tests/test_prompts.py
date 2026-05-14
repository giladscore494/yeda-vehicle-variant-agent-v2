from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.prompts import build_discovery_prompt, build_retry_discovery_prompt


SEED_HAVAL_H6 = {
    "make": "Haval",
    "model": "H6",
    "year_start": 2022,
    "year_end": 2026,
    "market": "IL",
}


def test_build_discovery_prompt_haval_h6_il():
    prompt = build_discovery_prompt(SEED_HAVAL_H6, market="IL")

    assert isinstance(prompt, str)
    assert prompt
    assert "Haval" in prompt
    assert "H6" in prompt
    assert "candidate_variants" in prompt
    assert "{" in prompt
    assert "}" in prompt


def test_build_retry_discovery_prompt_haval_h6_il():
    prompt = build_retry_discovery_prompt(SEED_HAVAL_H6, market="IL")

    assert isinstance(prompt, str)
    assert prompt
    assert "Haval" in prompt
    assert "H6" in prompt
    assert "candidate_variants" in prompt
    assert "IMPORTANT — RETRY ATTEMPT" in prompt
    assert "{" in prompt
    assert "}" in prompt
