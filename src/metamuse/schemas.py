"""Deterministic model-to-schema generator; run from the repository root."""

import hashlib
import json
from pathlib import Path

from metamuse import models as m
from metamuse.store import TABLE_MODELS


def schema_for(model: type[m.Model]) -> dict:
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema()}


def generated_schemas() -> dict[str, dict]:
    schemas = {
        f"{name}.input.json": schema_for(model) for name, model in m.TOOL_REQUEST_MODELS.items()
    }
    schemas["envelope.json"] = schema_for(m.Envelope)
    for model in (
        *TABLE_MODELS.values(),
        m.Capability,
        m.EvidenceRef,
        m.JobResult,
        m.AuthImportRequest,
        m.PermitCreateRequest,
    ):
        schemas[f"{model.__name__}.json"] = schema_for(model)
    return schemas


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def design_rollup(root: Path) -> str:
    """Narrative comes from reviewed numbered docs; never maintain a hand-synced copy."""
    header = (
        "# MetaMuse CLI — Plan、Spec、SDS 合併閱讀版\n\n"
        "文件版1.1。由 numbered docs 01_PLAN、02_SPEC、03_SDS、06_SOURCES 產生；"
        "各分檔是 authoritative source。待實作設計，未驗收真實帳號。\n\n"
    )
    return (
        header
        + "\n\n---\n\n"
        + "\n\n---\n\n".join(
            (root / "docs/design" / name).read_text(encoding="utf-8").rstrip()
            for name in ("01_PLAN.md", "02_SPEC.md", "03_SDS.md", "06_SOURCES.md")
        )
        + "\n"
    )


def main() -> None:
    root = Path.cwd()
    destination = root / "src/metamuse/data/schemas"
    for name, schema in generated_schemas().items():
        write_json(destination / name, schema)
    envelope = schema_for(m.Envelope)
    design = root / "docs/design/contracts"
    write_json(design / "envelope.schema.json", envelope)
    tools_path = design / "mcp-tools.json"
    tools = json.loads(tools_path.read_text())
    for tool in tools["tools"]:
        tool["inputSchema"] = schema_for(m.TOOL_REQUEST_MODELS[tool["name"]])
        tool["outputSchema"] = envelope
    write_json(tools_path, tools)
    (design.parent / "MetaMuse_CLI_Plan_Spec_SDS_v1.1.md").write_text(
        design_rollup(root), encoding="utf-8"
    )
    manifest_path = design.parent / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    for record in manifest["files"]:
        content = (design.parent / record["path"]).read_bytes()
        record.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    write_json(manifest_path, manifest)


if __name__ == "__main__":
    main()
