"""Regression tests for M4: ``.env.test`` must stay out of the repo.

The committed weak test secret (``SECRET_KEY=test-secret-key-do-not-use-in-production``)
was a vulnerability because ``.env.test`` was tracked in git, so anyone with the
source could read the predictable JWT signing key and forge admin tokens. The fix
added ``.env.test`` to ``.gitignore`` and removed it from the index. These tests
lock that in so a future commit that re-adds the file is caught.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_env_test_is_ignored_by_git():
    # ``git check-ignore`` exits 0 and prints the path when a file is ignored.
    result = _git("check-ignore", ".env.test")
    assert result.returncode == 0, (
        f".env.test is not ignored by git: {result.stderr.strip()!r}"
    )
    assert ".env.test" in result.stdout


def test_env_test_is_not_tracked():
    # ``git ls-files`` lists tracked files; an empty output means untracked.
    result = _git("ls-files", ".env.test")
    assert result.returncode == 0
    assert result.stdout.strip() == "", (
        ".env.test is still tracked in git -- the M4 fix has regressed"
    )


def test_gitignore_contains_env_test_pattern():
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.exists(), ".gitignore is missing"
    patterns = {line.strip() for line in gitignore.read_text().splitlines() if line.strip()}
    assert ".env.test" in patterns, ".gitignore no longer lists .env.test"
