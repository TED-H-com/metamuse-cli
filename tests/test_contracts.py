import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as SchemaError
from pydantic import ValidationError

from metamuse import app
from metamuse import models as m
from metamuse.schemas import generated_schemas, schema_for

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_json_preserves_strings():
    value = {"z": "繁中😀\n", "a": "e\u0301"}
    assert m.canonical_json(value) == '{"a":"é","z":"繁中😀\\n"}'.encode()
    assert m.payload_digest(value) == m.payload_digest(dict(reversed(list(value.items()))))
    assert m.payload_digest({"a": "e\u0301"}) != m.payload_digest({"a": "é"})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), {1: "invalid"}])
def test_non_json_values_rejected(value):
    with pytest.raises(ValueError):
        m.canonical_json({"value": value})


def test_schema_drift():
    checked = ROOT / "src/metamuse/data/schemas"
    schemas = generated_schemas()
    assert {p.name for p in checked.glob("*.json")} == set(schemas)
    for name, schema in schemas.items():
        assert json.loads((checked / name).read_text()) == schema, name
        Draft202012Validator.check_schema(schema)
    envelope = schema_for(m.Envelope)
    assert json.loads((ROOT / "docs/design/contracts/envelope.schema.json").read_text()) == envelope
    tools = json.loads((ROOT / "docs/design/contracts/mcp-tools.json").read_text())["tools"]
    assert len(tools) == 15
    for tool in tools:
        assert tool["inputSchema"] == schema_for(m.TOOL_REQUEST_MODELS[tool["name"]])
        assert tool["outputSchema"] == envelope


def test_design_examples_and_fake_approval():
    call = json.loads((ROOT / "docs/design/examples/tool-call.json").read_text())
    result = json.loads((ROOT / "docs/design/examples/tool-result.json").read_text())
    input_validator = Draft202012Validator(
        schema_for(m.CollectRequest), format_checker=FormatChecker()
    )
    input_validator.validate(call["arguments"])
    assert m.CollectRequest.model_validate(call["arguments"]).inputs[0].kind == "local_file"
    approved = copy.deepcopy(call["arguments"])
    approved["approved"] = True
    with pytest.raises(SchemaError):
        input_validator.validate(approved)
    with pytest.raises(ValidationError):
        m.CollectRequest.model_validate(approved)
    Draft202012Validator(schema_for(m.Envelope)).validate(result)
    m.Envelope.model_validate_json(json.dumps(result))


@pytest.mark.parametrize(
    "code,retry,expected_exit",
    [
        (m.ErrorCode.INPUT_INVALID, "never", 2),
        (m.ErrorCode.REFERENCE_INVALID, "never", 2),
        (m.ErrorCode.SCOPE_REQUIRED, "user_action", 3),
        (m.ErrorCode.SCOPE_DENIED, "user_action", 3),
        (m.ErrorCode.AUTH_REQUIRED, "user_action", 3),
        (m.ErrorCode.DEPENDENCY_MISSING, "user_action", 4),
        (m.ErrorCode.UNSUPPORTED, "never", 4),
        (m.ErrorCode.PROTOCOL_MISMATCH, "never", 4),
        (m.ErrorCode.ACCESS_DENIED, "user_action", 3),
        (m.ErrorCode.CHECKPOINT_REQUIRED, "user_action", 3),
        (m.ErrorCode.RATE_LIMITED, "safe_read", 5),
        (m.ErrorCode.SELECTOR_DRIFT, "never", 4),
        (m.ErrorCode.NETWORK_TIMEOUT, "safe_read", 5),
        (m.ErrorCode.JOB_BUSY, "user_action", 5),
        (m.ErrorCode.IDEMPOTENCY_CONFLICT, "never", 2),
        (m.ErrorCode.EXTERNAL_STATE_UNKNOWN, "reconcile", 6),
        (m.ErrorCode.STORAGE, "never", 7),
        (m.ErrorCode.LIMIT_REACHED, "never", 5),
        (m.ErrorCode.DATA_STALE, "user_action", 3),
        (m.ErrorCode.RESOURCE_UNAVAILABLE, "user_action", 3),
    ],
)
def test_all_error_codes_and_exit_mapping(code, retry, expected_exit):
    result = m.failure(m.CoreError(code, "Synthetic error", next_action="Review fixture"))
    assert result.error.retry_class == retry
    assert m.exit_code(result) == expected_exit
    assert m.Envelope.model_validate(result.model_dump(mode="json")).error.code == code
    Draft202012Validator(schema_for(m.Envelope)).validate(result.model_dump(mode="json"))
    assert len(m.ERROR_POLICY) == 20


def test_envelope_consistency():
    result = m.Envelope(status="ok", data={})
    assert m.exit_code(result) == 0
    assert m.exit_code(m.Envelope(status="partial")) == 5
    for invalid in [
        dict(status="error"),
        dict(status="ok", error=m.CoreError(m.ErrorCode.STORAGE, "x").detail),
    ]:
        with pytest.raises(ValueError):
            m.Envelope(**invalid)
    data = result.model_dump(mode="json")
    data["status"] = "error"
    with pytest.raises(SchemaError):
        Draft202012Validator(schema_for(m.Envelope)).validate(data)
    for field in ("data", "error", "warnings", "next_cursor", "contract_version", "request_id"):
        bad = result.model_dump(mode="json")
        del bad[field]
        with pytest.raises(SchemaError):
            Draft202012Validator(schema_for(m.Envelope)).validate(bad)


def test_capability_and_stub(ctx):
    capabilities = app.inspect_capabilities(ctx).data["capabilities"]
    assert len(capabilities) == 15
    assert sum(c["implementation"] == "implemented" for c in capabilities) == 5
    assert all(not c["effective"] for c in capabilities if c["implementation"] == "absent")
    request = json.loads((ROOT / "docs/design/examples/tool-call.json").read_text())["arguments"]
    assert app.submit_collect(ctx, request).error.code == m.ErrorCode.UNSUPPORTED
    assert (
        app.submit_collect(ctx, {**request, "approved": True}).error.code
        == m.ErrorCode.INPUT_INVALID
    )
    assert set(app.TOOL_FUNCTIONS) == set(m.TOOL_REQUEST_MODELS)


def test_config_defaults_and_example():
    import tomllib

    config = app.Config.model_validate(
        tomllib.loads((ROOT / "docs/design/examples/metamuse.example.toml").read_text())
    )
    assert config.app.network_mode == "offline"
    assert not config.muse.enabled and not config.publishing.enabled
    assert config.limits.text_max_bytes == 20 * 1024 * 1024
    assert config.limits.safe_read_retries == 2
    with pytest.raises(ValidationError):
        m.JobListRequest(project_id="prj_demo", limit=201)
