"""Deterministic model-to-schema generator; run from the repository root."""

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


if __name__ == "__main__":
    main()
