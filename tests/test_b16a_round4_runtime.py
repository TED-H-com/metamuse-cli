"""Round-4 credentials across packet, bundle, previews and fixture Muse payloads."""

import base64
import random

import pytest
from test_b16a_privacy import evidence
from test_muse_lifecycle import fixture_ctx as fixture_ctx  # noqa: F401
from test_muse_lifecycle import invoke, opened, packet

from metamuse import app
from metamuse import models as m


def surface_cases():
    templates = {
        "form_quote": "password=Ab3%22{v}&user=bob",
        "form_space": "password=correct%20horse%20{v}&user=bob",
        "curl_form": "curl -d 'password=Ab3%2C{v}&user=bob' https://example.invalid/login",
        "first_semicolon": "password=%3B{v}&user=bob",
        "first_bracket": "password=%5D{v}&user=bob",
        "xml_cdata": "<password><![CDATA[{v}]]></password>",
        "wsse_password": "<wsse:Password>{v}</wsse:Password>",
        "py_subscript": "cfg['password'] = '{v}'",
        "go_single_dash_flag": "-token={v}",
        "fullwidth_colon": "password：{v}",
        "aws_credentials_file": "AWS_SECRET_ACCESS_KEY={v}",
        "openai_api_key_env": "OPENAI_API_KEY={v}",
        "unquoted_dquote_tail": 'password=Ab3"{v} next',
        "form_flag": "q=--password+{v}",
        "form_curl_user": "q=curl+-u+user%3A{v}",
    }
    for name, template in templates.items():
        value = f"{name}_MARKER_SYNTHETIC_ONLY"
        yield name, template.format(v=value), value
    rng = random.Random(1014)
    while True:
        value = base64.b64encode(rng.randbytes(24)).decode()
        if "+" in value:
            break
    yield "bearer_plus_after_q", "Is this valid? Bearer " + value, value


def test_r3_round4_all_content_surfaces(fixture_ctx, project, monkeypatch, caplog):
    import test_b16a_privacy as privacy

    cases = {name: (text, value, "unused") for name, text, value in surface_cases()}
    monkeypatch.setattr(privacy, "SHAPES", tuple(cases))
    monkeypatch.setattr(privacy, "credential_shape", cases.__getitem__)
    privacy.test_packet_bundle_fixture_outbound_jobs_and_mcp(
        fixture_ctx, project, monkeypatch, caplog, exact_count=False
    )


@pytest.mark.parametrize("name,text,value", list(surface_cases()), ids=lambda value: value[:35])
def test_r3_round4_questions_and_topics_refuse(fixture_ctx, project, name, text, value):
    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    pkt = packet(ctx, project)
    requests = [
        (
            "send",
            m.MuseSendRequest(
                discussion_id=opening.data["discussion_id"],
                packet_id=pkt.id,
                question=text,
                request_key=name + "-send",
            ),
        ),
        ("open", m.MuseOpenRequest(project_id=project, topic=text, request_key=name + "-topic")),
    ]
    for operation, request in requests:
        result = invoke(ctx, operation, request)
        assert result.error.code == m.ErrorCode.INPUT_INVALID
        assert result.approval is None and value not in result.model_dump_json()


def test_r33_packet_and_bundle_form_context(fixture_ctx, project):
    value = f"{'context'}_MARKER_SYNTHETIC_ONLY"
    ref = evidence(fixture_ctx, project, "q=--password+" + value)
    result = app.prepare_packet(
        fixture_ctx, {"project_id": project, "selection": [ref], "request_key": "form-context"}
    )
    assert result.status == "ok"
    assert value not in result.model_dump_json()
