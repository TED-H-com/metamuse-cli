"""INTEGRATION/FIXTURE: collect → packet → Muse follow-up → cited draft → Page receipt."""

import json

import anyio
import test_facebook_approval
import tomlkit

entry = test_facebook_approval.entry


def test_one_workspace_collect_muse_followup_draft_and_publication(entry):
    token = entry.inputs / "page-token.txt"
    token.write_bytes(b"SYNTHETIC_PAGE_TOKEN")
    token.chmod(0o600)
    configured = entry.cli(
        "connect",
        "publishing",
        "--account",
        "synthetic-page",
        "--auth-file",
        token,
        "--page",
        "123456",
        "--api-version",
        "v25.0",
        operator=True,
    )
    assert configured["data"]["account_verified"] is False
    path = entry.workspace / "metamuse.toml"
    config = tomlkit.parse(path.read_text())
    # Explicit test-only backend. The process hook injects only a fixture Page gateway.
    config["publishing"]["backend"] = "fixture"
    path.write_text(tomlkit.dumps(config))

    async def scenario():
        observations = []
        async with entry.server(env=entry.env) as session:
            await entry.wait_worker_ready(session)
            collection = test_facebook_approval.request(entry)
            review = (await entry.call(session, "collect", collection, error=True))["approval"]
            assert not entry.events.exists()
            permit = entry.approve(review, review["request"], "approve-facebook")["data"]["id"]
            accepted = await entry.call(
                session,
                "collect",
                {
                    **collection,
                    "access_grant_id": permit,
                },
            )
            job = await entry.terminal(session, accepted["data"]["job_id"])
            assert job["job"]["state"] == "succeeded"
            results = (
                await entry.call(
                    session,
                    "search",
                    {
                        "project_id": entry.project,
                        "query": "測試活動",
                    },
                )
            )["data"]["results"]
            refs = [item["ref"] for item in results]
            assert len(refs) == 2
            packet = (
                await entry.call(
                    session,
                    "packet_prepare",
                    {
                        "project_id": entry.project,
                        "selection": refs,
                        "request_key": "packet",
                    },
                )
            )["data"]
            assert "facebook_post_permalink" in packet["rendered_text"]
            opened = (
                await entry.approved_call(
                    session,
                    "muse.open",
                    {
                        "project_id": entry.project,
                        "topic": "測試活動內容整理",
                        "request_key": "open",
                    },
                    "approve-open",
                )
            )["data"]
            await entry.terminal(session, opened["job_id"])
            for number, question in enumerate(("請列出公告和留言的重點。", "還缺哪些資料？"), 1):
                sent = (
                    await entry.approved_call(
                        session,
                        "muse.send",
                        {
                            "discussion_id": opened["discussion_id"],
                            "packet_id": packet["id"],
                            "question": question,
                            "request_key": f"send-{number}",
                        },
                        f"approve-send-{number}",
                    )
                )["data"]
                await entry.terminal(session, sent["job_id"])
                refreshed = (
                    await entry.approved_call(
                        session,
                        "muse.read",
                        {
                            "exchange_id": sent["exchange_id"],
                            "refresh": True,
                        },
                        f"approve-read-{number}",
                    )
                )["data"]
                await entry.terminal(session, refreshed["refresh_job_id"])
                readback = (
                    await entry.call(
                        session,
                        "muse_read",
                        {
                            "exchange_id": sent["exchange_id"],
                        },
                    )
                )["data"]
                assert readback["mock"] is True
                observation = next(
                    o
                    for o in readback["observations"]
                    if o["raw_event"].get("payload", {}).get("role") == "assistant"
                    and o["raw_event"]["message_id"].startswith(
                        readback["exchange"]["request_marker"]
                    )
                )
                observations.append(observation["evidence_ref"])
                await entry.call(
                    session,
                    "muse_close_exchange",
                    {
                        "exchange_id": sent["exchange_id"],
                        "outcome": "answered",
                        "request_key": f"close-{number}",
                    },
                )
            text = "測試活動公告與留言已整理。這是經過兩輪討論的測試草稿。"
            saved = (
                await entry.call(
                    session,
                    "draft_save",
                    {
                        "project_id": entry.project,
                        "text": text,
                        "platform": "facebook",
                        "facts_refs": [*refs, *observations],
                        "request_key": "draft",
                    },
                )
            )["data"]
            assert saved["mock"] is True and len(saved["muse_evidence"]) == 2
            publish = {
                "project_id": entry.project,
                "draft_id": saved["draft_id"],
                "revision": 1,
                "account": "synthetic-page",
                "destination": "123456",
            }
            preview = (await entry.call(session, "publication_preview", publish))["data"]
            assert preview["text"] == text
            review = preview["approval"]
            grant = entry.approve(review, review["request"], "approve-publish")["data"]["id"]
            submitted = (
                await entry.call(
                    session,
                    "publication_submit",
                    {
                        **publish,
                        "request_key": "post",
                        "publication_grant": grant,
                    },
                )
            )["data"]
            job = await entry.terminal(session, submitted["job_id"])
            assert job["job"]["state"] == "succeeded"
        # The receipt must survive a server restart, with no second post attempt.
        async with entry.server(env=entry.env) as session:
            result = (
                await entry.call(
                    session,
                    "publication_get",
                    {
                        "publication_id": submitted["publication_id"],
                    },
                )
            )["data"]
            assert result["state"] == "sent_verified" and result["live_verified"] is False
            assert result["target"]["backend"] == "fixture"
            assert result["remote_id"] == "123456_98765"
            assert result["payload"]["message"] == text
            assert len(result["attempts"]) == 1
            preview = (
                await entry.call(
                    session,
                    "draft_preview",
                    {
                        "draft_id": saved["draft_id"],
                        "revision": 1,
                    },
                )
            )["data"]
            assert preview["text"] == text and len(preview["muse_evidence"]) == 2

    anyio.run(scenario)
    events = [json.loads(line) for line in entry.events.read_text().splitlines()]
    assert [event["event"] for event in events] == ["browser", "post"]
    entry.hygiene()
