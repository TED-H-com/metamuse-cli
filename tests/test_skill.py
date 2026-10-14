"""Tests for B15 neutral metamuse Skill (FR-015, FR-016, FR-018, AT-023, AT-027).

Evidence class: OFFLINE (text/link checks) and INTEGRATION (MCP call_tool invariant).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import metamuse
from metamuse import models as m
from metamuse.app import AppContext, inspect_capabilities
from metamuse.cli import parser
from metamuse.mcp import call_tool

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills/metamuse"
SKILL_FILE = SKILL_DIR / "SKILL.md"
DESIGN_SKILL_FILE = REPO_ROOT / "docs/design/skills/metamuse/SKILL.md"

CONTRACT_TOOLS = frozenset(
    {
        "metamuse_inspect",
        "metamuse_project_create",
        "metamuse_collect",
        "metamuse_job_get",
        "metamuse_job_list",
        "metamuse_job_control",
        "metamuse_search",
        "metamuse_packet_prepare",
        "metamuse_muse_open",
        "metamuse_muse_send",
        "metamuse_muse_read",
        "metamuse_muse_close_exchange",
        "metamuse_draft_save",
        "metamuse_draft_preview",
        "metamuse_export",
    }
)

STRATEGY_TERMS = (
    "cta",
    "call to action",
    "人設",
    "persona",
    "黃金時段",
    "最佳時段",
    "best time to post",
    "爆款",
    "viral",
    "hook",
    "公式",
)

FORBIDDING_PATTERNS = (
    "不加",
    "不可",
    "禁止",
    "不包含",
    "不提供",
    "不支援",
    "不帶",
    "無",
    "forbid",
    "never",
)

VALID_SAMPLE_PAYLOADS: dict[str, dict[str, Any]] = {
    "metamuse_inspect": {},
    "metamuse_project_create": {
        "name": "Synthetic project",
        "request_key": "req-project-1",
    },
    "metamuse_collect": {
        "project_id": "prj_01234567890123456789012345678901",
        "inputs": [{"kind": "local_file", "path": "synthetic.jsonl", "format": "jsonl"}],
        "resources": ["comments"],
        "limits": {
            "max_urls": 10,
            "max_items": 500,
            "max_bytes": 1073741824,
            "wall_clock_seconds": 900,
        },
        "access_grant_id": None,
        "request_key": "req-collect-1",
    },
    "metamuse_job_get": {
        "job_id": "job_01234567890123456789012345678901",
    },
    "metamuse_job_list": {
        "project_id": "prj_01234567890123456789012345678901",
    },
    "metamuse_job_control": {
        "job_id": "job_01234567890123456789012345678901",
        "operation": "resume",
        "request_key": "req-control-1",
    },
    "metamuse_search": {
        "project_id": "prj_01234567890123456789012345678901",
        "query": "synthetic search query",
    },
    "metamuse_packet_prepare": {
        "project_id": "prj_01234567890123456789012345678901",
        "selection": [
            {"kind": "item_revision", "id": "itm_01234567890123456789012345678901", "revision": 1}
        ],
        "request_key": "req-packet-1",
    },
    "metamuse_muse_open": {
        "project_id": "prj_01234567890123456789012345678901",
        "topic": "Synthetic research topic",
        "access_grant_id": "pmt_01234567890123456789012345678901",
        "request_key": "req-muse-open-1",
    },
    "metamuse_muse_send": {
        "discussion_id": "dsc_01234567890123456789012345678901",
        "packet_id": "pkt_01234567890123456789012345678901",
        "question": "What evidence supports this finding?",
        "share_grant_id": "pmt_01234567890123456789012345678901",
        "request_key": "req-muse-send-1",
    },
    "metamuse_muse_read": {
        "exchange_id": "exc_01234567890123456789012345678901",
        "refresh": True,
    },
    "metamuse_muse_close_exchange": {
        "exchange_id": "exc_01234567890123456789012345678901",
        "outcome": "answered",
        "request_key": "req-close-1",
    },
    "metamuse_draft_save": {
        "project_id": "prj_01234567890123456789012345678901",
        "text": "Synthetic neutral draft content",
        "platform": "generic",
        "facts_refs": [],
        "request_key": "req-draft-1",
    },
    "metamuse_draft_preview": {
        "draft_id": "drf_01234567890123456789012345678901",
        "revision": 1,
    },
    "metamuse_export": {
        "project_id": "prj_01234567890123456789012345678901",
        "selection": [
            {"kind": "item_revision", "id": "itm_01234567890123456789012345678901", "revision": 1}
        ],
        "format": "jsonl",
        "request_key": "req-export-1",
    },
}


def _get_valid_cli_commands() -> set[str]:
    root_parser = parser()
    subparsers_action = [
        a for a in root_parser._actions if hasattr(a, "choices") and a.dest == "command"
    ][0]
    valid_commands: set[str] = set()
    for cmd, subp in subparsers_action.choices.items():
        sub_sub = [a for a in subp._actions if hasattr(a, "choices") and a.dest == "action"]
        if sub_sub:
            for action in sub_sub[0].choices:
                valid_commands.add(f"{cmd} {action}")
        else:
            valid_commands.add(cmd)
    return valid_commands


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    lines = content.splitlines()
    assert lines and lines[0].strip() == "---", "SKILL.md must start with frontmatter '---'"
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    assert end_idx is not None, "SKILL.md frontmatter must be closed with '---'"

    data: dict[str, Any] = {}
    current_section: str | None = None
    for line in lines[1:end_idx]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith("  ") and current_section:
            k, v = line.strip().split(":", 1)
            data.setdefault(current_section, {})[k.strip()] = v.strip().strip("\"'")
        elif ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            if not v:
                current_section = k
                data[k] = {}
            else:
                current_section = None
                data[k] = v.strip("\"'")
    body = "\n".join(lines[end_idx + 1 :])
    return data, body


def test_frontmatter_and_line_limits():
    assert SKILL_FILE.exists(), f"Missing {SKILL_FILE}"
    content = SKILL_FILE.read_text(encoding="utf-8")
    lines = content.splitlines()
    assert len(lines) <= 100, f"SKILL.md must be <= 100 lines, got {len(lines)}"

    fm, _ = _parse_frontmatter(content)
    assert fm.get("name") == "metamuse", f"Expected name 'metamuse', got {fm.get('name')}"

    desc = fm.get("description", "")
    assert isinstance(desc, str) and desc, "Missing description in frontmatter"
    assert "\n" not in desc, "Description must be a single line"
    assert len(desc) <= 1024, f"Description must be <= 1024 chars, got {len(desc)}"
    assert not re.search(r"<[^>]+>", desc), "Description must not contain XML/HTML tags"

    metadata = fm.get("metadata", {})
    assert isinstance(metadata, dict), "Missing metadata section in frontmatter"
    pkg_ver = metamuse.__version__
    meta_ver = metadata.get("version")
    assert meta_ver == pkg_ver, f"Frontmatter version {meta_ver} does not match {pkg_ver}"


def test_design_copy_is_byte_identical():
    assert SKILL_FILE.exists()
    assert DESIGN_SKILL_FILE.exists()
    assert SKILL_FILE.read_bytes() == DESIGN_SKILL_FILE.read_bytes(), (
        "docs/design/skills/metamuse/SKILL.md must be byte-identical to skills/metamuse/SKILL.md"
    )


def test_relative_links_resolve():
    all_files = list(SKILL_DIR.rglob("*.md"))
    assert all_files, "No markdown files found under skills/metamuse"
    link_pattern = re.compile(r"\]\(([^)]+)\)")

    checked_count = 0
    for doc in all_files:
        text = doc.read_text(encoding="utf-8")
        for match in link_pattern.finditer(text):
            target = match.group(1).strip()
            if (
                target.startswith("http://")
                or target.startswith("https://")
                or target.startswith("#")
                or target.startswith("mailto:")
            ):
                continue
            path_part = target.split("#", 1)[0]
            resolved = (doc.parent / path_part).resolve()
            assert resolved.exists(), f"Broken relative link in {doc}: {target} -> {resolved}"
            checked_count += 1
    assert checked_count >= 8, f"Expected relative links to be checked, found {checked_count}"


def test_contract_tool_names_and_tools_md_coverage():
    all_files = list(SKILL_DIR.rglob("*.md"))
    tool_pattern = re.compile(r"\bmetamuse_[a-z0-9_]+\b")

    for doc in all_files:
        text = doc.read_text(encoding="utf-8")
        found_tools = set(tool_pattern.findall(text))
        unknown_tools = found_tools - CONTRACT_TOOLS
        assert not unknown_tools, f"Found unknown metamuse_* tokens in {doc}: {unknown_tools}"

    tools_doc = SKILL_DIR / "references/tools.md"
    assert tools_doc.exists(), f"Missing {tools_doc}"
    tools_text = tools_doc.read_text(encoding="utf-8")
    tools_in_reference = set(tool_pattern.findall(tools_text))
    missing = CONTRACT_TOOLS - tools_in_reference
    assert not missing, f"references/tools.md does not cover all tools. Missing: {missing}"


def test_cli_commands_exist_in_parser():
    valid_commands = _get_valid_cli_commands()
    all_files = list(SKILL_DIR.rglob("*.md"))
    cmd_pattern = re.compile(r"`metamuse\s+([^`]+)`")

    checked_commands = 0
    for doc in all_files:
        text = doc.read_text(encoding="utf-8")
        for match in cmd_pattern.finditer(text):
            raw_args = match.group(1).strip().split()
            cmd_args = [tok for tok in raw_args if not tok.startswith("-")]
            if not cmd_args:
                continue
            two_word = " ".join(cmd_args[:2])
            one_word = cmd_args[0]
            if two_word in valid_commands:
                matched_cmd = two_word
            elif one_word in valid_commands:
                matched_cmd = one_word
            else:
                pytest.fail(f"Unknown CLI command in {doc}: 'metamuse {match.group(1)}'")
            assert matched_cmd in valid_commands
            checked_commands += 1
    assert checked_commands >= 15, f"Expected CLI commands in skill, checked {checked_commands}"


def test_error_codes_exist_in_models():
    all_files = list(SKILL_DIR.rglob("*.md"))
    err_pattern = re.compile(r"\bE_[A-Z0-9_]+\b")
    valid_codes = {item.value for item in m.ErrorCode}

    checked_codes = 0
    for doc in all_files:
        text = doc.read_text(encoding="utf-8")
        for match in err_pattern.finditer(text):
            code = match.group(0)
            assert code in valid_codes, f"Unknown error code {code} in {doc}"
            checked_codes += 1
    assert checked_codes >= 3, f"Expected error code references, checked {checked_codes}"


def test_inspect_doctor_precedes_other_tools_in_skill():
    text = SKILL_FILE.read_text(encoding="utf-8")
    inspect_pos = text.find("metamuse_inspect")
    doctor_pos = text.find("metamuse --json doctor")
    if doctor_pos == -1:
        doctor_pos = text.find("metamuse doctor")

    assert inspect_pos != -1, "metamuse_inspect not mentioned in SKILL.md"
    assert doctor_pos != -1, "metamuse doctor not mentioned in SKILL.md"

    first_entry = min(inspect_pos, doctor_pos)
    other_tools = CONTRACT_TOOLS - {"metamuse_inspect"}
    for tool in other_tools:
        pos = text.find(tool)
        if pos != -1:
            assert pos > first_entry, (
                f"Tool {tool} appeared at index {pos}, before inspect/doctor at {first_entry}"
            )


def test_neutrality_strategy_terms_only_on_forbidding_lines():
    all_files = list(SKILL_DIR.rglob("*.md"))
    violations: list[str] = []

    for doc in all_files:
        lines = doc.read_text(encoding="utf-8").splitlines()
        for line_no, line in enumerate(lines, start=1):
            lower_line = line.lower()
            matching_terms = [term for term in STRATEGY_TERMS if term in lower_line]
            if matching_terms:
                is_forbidding = any(pat in line for pat in FORBIDDING_PATTERNS)
                if not is_forbidding:
                    violations.append(f"{doc}:{line_no} terms {matching_terms}: {line}")
    assert not violations, "Strategy term neutrality violations:\n" + "\n".join(violations)


def test_at027_runtime_invariant_ineffective_tools_return_is_error(tmp_path):
    ctx = AppContext.open(tmp_path)
    inspect_env = inspect_capabilities(ctx)
    assert inspect_env.status == "ok"
    capabilities = {c["name"]: c for c in inspect_env.data["capabilities"]}

    ineffective_tested = 0
    for name, req_model in m.TOOL_REQUEST_MODELS.items():
        assert name in VALID_SAMPLE_PAYLOADS, f"Missing sample payload for {name}"
        payload = VALID_SAMPLE_PAYLOADS[name]
        validated_req = req_model.model_validate(payload)
        assert validated_req is not None

        cap = capabilities[name]
        if cap["effective"]:
            continue

        result = call_tool(ctx, name, payload)
        assert result.is_error is True, f"Tool {name} effective:false did not return is_error"
        structured = result.structured_content
        assert structured["status"] == "error"
        assert structured["status"] not in ("ok", "accepted", "partial")
        err = structured.get("error")
        assert err is not None, f"Tool {name} error envelope missing error object"
        assert err["code"] not in (m.ErrorCode.INPUT_INVALID, m.ErrorCode.REFERENCE_INVALID), (
            f"Tool {name} returned input validation error {err['code']}"
        )
        assert err["code"] in {code.value for code in m.ErrorCode}
        ineffective_tested += 1

    assert ineffective_tested >= 1, "Expected at least one ineffective tool for AT-027"
