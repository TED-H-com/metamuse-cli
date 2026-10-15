"""Review shapes through all shared application frontends and public surfaces."""

import json
from urllib.parse import quote_plus

from test_muse_lifecycle import fixture_ctx as fixture_ctx  # noqa: F401
from test_muse_lifecycle import invoke

from metamuse import models as m


def surface_cases():
    from test_b16a_round5_blocks import block
    from test_b16a_round5_quotes import RULES

    marker = f"{'SURFACE'}_MARKER_R5_SYNTHETIC_ONLY"
    bodies = {
        "userinfo_comma": f"https://example.invalid,https://alice:{marker}@example.invalid/a",
        "userinfo_semicolon": f"https://example.invalid;https://alice:{marker}@example.invalid/a",
        "userinfo_json": json.dumps(
            {
                "public": "https://example.invalid",
                "private": f"https://alice:{marker}@example.invalid/a",
            }
        ),
        "har_earlier_percent": json.dumps(
            {
                "url": "https://example.invalid/?q=%22x",
                "cookies": [{"name": "theme", "value": marker}],
            }
        ),
        "har_earlier_escape": json.dumps(
            [{"name": "q", "value": '5" screen'}, {"name": "password", "value": marker}]
        ),
        "har_value_first": json.dumps({"value": "Ab3%22" + marker, "name": "sid"}),
        "har_form": "q="
        + quote_plus(json.dumps({"cookies": [{"name": "theme", "value": marker}]})),
        "nested_shell": 'password=${X:-"api_key=' + marker + '"}',
        "nested_call": 'token=os.getenv("T", "password=' + marker + '")',
        "nested_angle": 'password="<password=' + marker + '>"',
    }
    for kind in ("putty", "pgp", "pem"):
        bodies[kind + "_json"] = json.dumps({"content": block(kind, marker)})
        bodies[kind + "_inline"] = "key_file: " + block(kind, marker)
        bodies[kind + "_form"] = "q=" + quote_plus(block(kind, marker))
    for index, rule in enumerate(RULES):
        bodies["quoted_" + str(index)] = json.dumps({"cmd": rule.format(v=marker)})
    for name, text in bodies.items():
        yield name, text, marker
    number = f"{98765432}{10123456}"
    yield "har_numeric", '{"name":"sid","value":' + number + "}", number


def test_r5_packet_bundle_outbound_jobs_and_mcp(fixture_ctx, project, monkeypatch, caplog):
    import test_b16a_privacy as privacy

    cases = {name: (text, value, "unused") for name, text, value in surface_cases()}
    monkeypatch.setattr(privacy, "SHAPES", tuple(cases))
    monkeypatch.setattr(privacy, "credential_shape", cases.__getitem__)
    privacy.test_packet_bundle_fixture_outbound_jobs_and_mcp(
        fixture_ctx,
        project,
        monkeypatch,
        caplog,
        exact_count=False,
        separate_records=True,
    )


def test_r5_question_topic_refusal_before_preview_or_writes(fixture_ctx, project):
    ctx = fixture_ctx
    tables = ("jobs", "discussions", "exchanges", "permits", "events", "request_receipts")
    with ctx.store.connection() as connection:
        before = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tables
        }
    for name, text, value in surface_cases():
        for operation, request in (
            (
                "send",
                m.MuseSendRequest(
                    discussion_id="missing",
                    packet_id="missing",
                    question=text,
                    request_key=name + "-send",
                ),
            ),
            (
                "open",
                m.MuseOpenRequest(project_id=project, topic=text, request_key=name + "-topic"),
            ),
        ):
            result = invoke(ctx, operation, request)
            assert result.error.code == m.ErrorCode.INPUT_INVALID, (name, result)
            assert result.approval is None and value not in result.model_dump_json()
    with ctx.store.connection() as connection:
        for table in tables:
            assert (
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == before[table]
            )
    assert ctx.muse_gateway.calls == []
