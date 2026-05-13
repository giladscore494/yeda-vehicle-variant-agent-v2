"""GitHub canonical store (adapted from legacy storage/github_canonical_store.py).

Pushes ONLY the canonical resume package to a GitHub repository. Reads
credentials from Streamlit secrets (`GITHUB_TOKEN`, `GITHUB_REPO`,
`GITHUB_BRANCH`, `CANONICAL_RESUME_PATH`, `CANONICAL_BACKUP_PATH`).

This module never pushes data/output, data/cache, batch_state.json,
problem_queue.json or any other state file.
"""
from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import quote

import requests


def _read_streamlit_secrets() -> dict:
    try:  # pragma: no cover - streamlit is optional
        import streamlit as st
    except Exception:
        return {}
    try:
        return dict(st.secrets)
    except Exception:
        return {}


def get_github_config() -> dict:
    secrets = _read_streamlit_secrets()
    return {
        "token": secrets.get("GITHUB_TOKEN"),
        "repo": secrets.get("GITHUB_REPO"),
        "branch": secrets.get("GITHUB_BRANCH", "main"),
        "canonical_path": secrets.get("CANONICAL_RESUME_PATH", "data/canonical/resume_package_canonical.json"),
        "backup_path": secrets.get("CANONICAL_BACKUP_PATH", "data/canonical/resume_package_backup_previous.json"),
    }


def _contents_url(repo: str, path: str) -> str:
    return f"https://api.github.com/repos/{repo}/contents/{quote(path)}"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_existing_sha(path: str, cfg: dict) -> tuple[str | None, str | None]:
    """Return (sha, error). sha is None when the file does not yet exist."""
    token = cfg.get("token")
    repo = cfg.get("repo")
    if not token or not repo:
        return None, "GitHub credentials are missing."
    try:  # pragma: no cover - network
        resp = requests.get(
            _contents_url(repo, path),
            headers=_headers(token),
            params={"ref": cfg.get("branch", "main")},
            timeout=30,
        )
    except Exception as exc:
        return None, f"Failed to fetch file from GitHub: {type(exc).__name__}"
    if resp.status_code == 404:
        return None, None
    if resp.status_code >= 400:
        return None, f"Failed to fetch file from GitHub (HTTP {resp.status_code})."
    body = resp.json() if resp.content else {}
    return body.get("sha"), None


def push_file_to_github(path: str, data: Any, commit_message: str) -> dict:
    cfg = get_github_config()
    token = cfg.get("token")
    repo = cfg.get("repo")
    branch = cfg.get("branch", "main")
    if not token or not repo:
        return {"ok": False, "error": "GitHub credentials are missing in Streamlit secrets."}
    sha, fetch_err = _get_existing_sha(path, cfg)
    if fetch_err:
        return {"ok": False, "error": fetch_err}
    content_b64 = base64.b64encode(
        json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("utf-8")
    payload: dict = {"message": commit_message, "content": content_b64, "branch": branch}
    if sha:
        payload["sha"] = sha
    try:  # pragma: no cover - network
        resp = requests.put(_contents_url(repo, path), headers=_headers(token), json=payload, timeout=30)
    except Exception as exc:
        return {"ok": False, "error": f"Failed to push file to GitHub: {type(exc).__name__}"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"Failed to push file to GitHub (HTTP {resp.status_code})."}
    body = resp.json() if resp.content else {}
    commit = body.get("commit") if isinstance(body, dict) else {}
    content_obj = body.get("content") if isinstance(body, dict) else {}
    return {
        "ok": True,
        "path": path,
        "sha": (content_obj or {}).get("sha"),
        "commit_sha": (commit or {}).get("sha"),
    }


def push_canonical(package: dict, commit_message: str | None = None) -> dict:
    """Push ONLY the canonical resume package."""
    if not isinstance(package, dict):
        return {"ok": False, "error": "canonical package is not a dict"}
    cfg = get_github_config()
    msg = commit_message or "Update canonical resume_package_canonical.json"
    return push_file_to_github(cfg["canonical_path"], package, msg)
