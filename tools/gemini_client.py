"""Gemini client + JSON salvage helpers (adapted from legacy tools/gemini_client.py).

Includes:
- parse_json_from_gemini_text(raw_text): tolerant JSON parser for Gemini output
- salvage_candidate_variants_from_raw(raw_text): recover complete candidates from
  malformed JSON streams
- GeminiClient: thin wrapper around google-genai with optional grounded search
"""
from __future__ import annotations

import json
import os
from typing import Any

REPAIR_PROMPT = (
    "Repair this malformed JSON into valid compact JSON. Do not add new facts. "
    "Do not infer. Preserve only complete candidate_variants and sources. If the "
    "last object is incomplete, drop that incomplete object. Return JSON only."
)

try:  # pragma: no cover - streamlit is optional at runtime
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

IMPORT_ERROR: str | None = None
try:  # pragma: no cover - google-genai is optional in tests
    from google import genai
    from google.genai import types
except Exception as exc:  # pragma: no cover
    genai = None
    types = None
    IMPORT_ERROR = str(exc)


def parse_json_from_gemini_text(raw_text: str):
    if raw_text is None or str(raw_text).strip() == "":
        return None, "empty raw_text"

    text = str(raw_text).lstrip("\ufeff").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    def _loads(candidate: str):
        parsed = json.loads(candidate)
        if isinstance(parsed, (dict, list)):
            return parsed, None
        return None, f"parsed JSON is {type(parsed).__name__}, expected dict/list"

    try:
        return _loads(text)
    except Exception as exc:
        direct_err = str(exc)

    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            unescaped = json.loads(text)
            if isinstance(unescaped, str):
                parsed, err = _loads(unescaped)
                if parsed is not None:
                    return parsed, None
        except Exception:
            pass

    first_obj, last_obj = text.find("{"), text.rfind("}")
    if first_obj != -1 and last_obj > first_obj:
        try:
            return _loads(text[first_obj : last_obj + 1])
        except Exception as exc:
            return None, f"failed to parse extracted object: {exc}; direct_error: {direct_err}"

    first_arr, last_arr = text.find("["), text.rfind("]")
    if first_arr != -1 and last_arr > first_arr:
        try:
            return _loads(text[first_arr : last_arr + 1])
        except Exception as exc:
            return None, f"failed to parse extracted array: {exc}; direct_error: {direct_err}"

    return None, f"failed to parse raw text: {direct_err}"


def salvage_candidate_variants_from_raw(raw_text: str):
    if not raw_text or '"candidate_variants"' not in raw_text:
        return None

    decoder = json.JSONDecoder()
    text = str(raw_text)
    salvage_sources: list = []

    def _skip_ws(i: int) -> int:
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        return i

    cand_key = text.find('"candidate_variants"')
    if cand_key == -1:
        return None

    src_key = text.find('"sources"')
    if src_key != -1 and src_key < cand_key:
        src_colon = text.find(":", src_key)
        if src_colon != -1:
            src_start = _skip_ws(src_colon + 1)
            if src_start < len(text) and text[src_start] == "[":
                try:
                    parsed_sources, _ = decoder.raw_decode(text, src_start)
                    if isinstance(parsed_sources, list):
                        salvage_sources = parsed_sources
                except Exception:
                    salvage_sources = []

    colon = text.find(":", cand_key)
    if colon == -1:
        return None
    arr_start = _skip_ws(colon + 1)
    if arr_start >= len(text) or text[arr_start] != "[":
        return None

    idx = arr_start + 1
    complete: list[dict] = []
    dropped = False
    while idx < len(text):
        idx = _skip_ws(idx)
        if idx >= len(text):
            break
        if text[idx] == "]":
            break
        if text[idx] == ",":
            idx += 1
            continue
        try:
            obj, next_idx = decoder.raw_decode(text, idx)
            if isinstance(obj, dict):
                complete.append(obj)
            idx = next_idx
        except Exception:
            dropped = True
            break

    if not complete:
        return None

    return {
        "search_queries": [],
        "sources": salvage_sources,
        "candidate_variants": complete,
        "conflicts": [],
        "unresolved": False,
        "_salvage": {
            "json_salvage_used": True,
            "dropped_incomplete_candidate": dropped,
            "salvaged_candidate_count": len(complete),
        },
    }


class GeminiClient:
    """Thin wrapper around google-genai. Reads credentials from Streamlit secrets or env."""

    def __init__(self) -> None:
        secrets = getattr(st, "secrets", None) if st else None

        def _secret(key: str):
            if secrets is None:
                return None
            try:
                return secrets.get(key)
            except Exception:
                return None

        self._secrets_api_key = _secret("GEMINI_API_KEY")
        self._env_api_key = os.getenv("GEMINI_API_KEY")
        self.api_key = self._secrets_api_key or self._env_api_key
        self.fast_model = _secret("GEMINI_MODEL_FAST") or os.getenv("GEMINI_MODEL_FAST", "gemini-3-flash-preview")
        self.strong_model = _secret("GEMINI_MODEL_STRONG") or os.getenv("GEMINI_MODEL_STRONG", "gemini-3-pro-preview")
        self.client = genai.Client(api_key=self.api_key) if (genai is not None and self.api_key) else None

    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def get_config_status(self) -> dict:
        has_key = self.has_api_key()
        return {
            "api_key": "found" if has_key else "missing",
            "google_genai_import_ok": bool(genai is not None),
            "client_ready": bool(self.client) and has_key,
            "import_error": IMPORT_ERROR,
            "fast_model": self.fast_model,
            "strong_model": self.strong_model,
            "grounding_supported": True,
        }

    def _response(self, *, model: str, ok: bool, error: str | None = None, data: Any = None,
                  raw_text: str | None = None, parsed_json: Any = None, parse_error: str | None = None,
                  json_salvage_used: bool = False, dropped_incomplete_candidate: bool = False) -> dict:
        return {
            "ok": ok,
            "provider": "gemini",
            "model": model,
            "grounding_requested": True,
            "request_attempted": True,
            "error": error,
            "data": data,
            "raw_text": raw_text,
            "parsed_json": parsed_json,
            "parse_error": parse_error,
            "json_salvage_used": json_salvage_used,
            "dropped_incomplete_candidate": dropped_incomplete_candidate,
        }

    def _validate_ready(self, model: str):
        if not self.has_api_key():
            return self._response(model=model, ok=False, error="GEMINI_API_KEY missing")
        if genai is None or types is None:
            return self._response(model=model, ok=False, error=f"google-genai import failed: {IMPORT_ERROR}")
        if self.client is None:
            return self._response(model=model, ok=False, error="Gemini client not initialized")
        return None

    def grounded_generate_json(self, prompt: str, *, strong: bool = False, model_override: str | None = None) -> dict:
        model = model_override or (self.strong_model if strong else self.fast_model)
        invalid = self._validate_ready(model)
        if invalid:
            return invalid
        try:  # pragma: no cover - network/SDK call
            config = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_mime_type="application/json",
                temperature=0.1,
            )
            response = self.client.models.generate_content(model=model, contents=prompt, config=config)
            raw_text = getattr(response, "text", "") or ""
            parsed_json, parse_error = parse_json_from_gemini_text(raw_text)
            if parsed_json is None and parse_error is not None:
                salvage = salvage_candidate_variants_from_raw(raw_text)
                if salvage and salvage.get("candidate_variants"):
                    parsed_json = salvage
                    parse_error = None
            ok = parsed_json is not None and parse_error is None
            salvage_meta = (parsed_json or {}).get("_salvage", {}) if isinstance(parsed_json, dict) else {}
            return self._response(
                model=model,
                ok=ok,
                error=(None if ok else parse_error),
                data=(parsed_json if ok else None),
                raw_text=raw_text,
                parsed_json=(parsed_json if ok else None),
                parse_error=(None if ok else parse_error),
                json_salvage_used=bool(salvage_meta.get("json_salvage_used", False)),
                dropped_incomplete_candidate=bool(salvage_meta.get("dropped_incomplete_candidate", False)),
            )
        except Exception as exc:  # pragma: no cover
            return self._response(model=model, ok=False, error=f"Gemini grounded call failed: {exc}")
