"""Workflow-hygiene tests (audit 2026-07, finding A10).

A `uses: some/action@v4` reference is mutable — whoever controls the tag
controls what runs with the workflow's token. Pinning to a commit SHA and
declaring least-privilege `permissions:` are cheap, and this test keeps
future workflow edits honest.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

_USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.MULTILINE)


def test_workflows_exist() -> None:
    assert WORKFLOWS, "no workflow files found — glob broken?"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_sha_pinned(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for reference in _USES.findall(text):
        _, _, version = reference.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", version), (
            f"{path.name}: {reference!r} is not pinned to a 40-hex commit SHA"
        )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_declares_permissions(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert re.search(r"^permissions:", text, re.MULTILINE), (
        f"{path.name}: no top-level permissions: block — the default token "
        "grant is broader than any job here needs"
    )
