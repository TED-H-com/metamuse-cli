"""Local import, identity, parents, and provenance (B05; AT-002..004, 013, 017, 041)."""

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from metamuse import app, collect, jobs
from metamuse import models as m

FIXTURES = Path(__file__).parent / "fixtures" / "collect"
LIMITS = {
    "max_urls": 10,
    "max_items": 500,
    "max_bytes": 1_073_741_824,
    "wall_clock_seconds": 900,
}
SENTINEL = "下次活動是否有字幕？SENTINEL"


def allow(ctx, *roots: Path) -> None:
    lines: list[str] = []
    for line in app.DEFAULT_CONFIG.splitlines():
        lines.append(line)
        if line.startswith("telemetry_enabled"):
            encoded = ", ".join(json.dumps(str(root)) for root in roots)
            lines.append(f"import_roots = [{encoded}]")
    path = ctx.paths.root / "metamuse.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config = app.Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    ctx.policy = config
    ctx.limits = config.limits


def request(project, inputs, resources, key="import-1", limits=None, **extra):
    body = {
        "project_id": project,
        "inputs": inputs,
        "resources": resources,
        "limits": m.CollectLimits(**(limits or LIMITS)),
        "access_grant_id": None,
        "request_key": key,
    }
    body.update(extra)
    return m.CollectRequest(**body)


def local(path: Path, fmt: str) -> m.LocalFileInput:
    return m.LocalFileInput(kind="local_file", path=str(path), format=fmt)


def submit(ctx, req):
    return app.submit_collect(ctx, req)


def finish(ctx, env):
    assert env.status == "accepted", env.error
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    return ctx.store.get(m.Job, env.data["job_id"])


def run(ctx, req):
    return finish(ctx, submit(ctx, req))


def data(job):
    return job.result["data"]["steps"][0]["data"]


def q(ctx, sql, args=()):
    with ctx.store.connection() as conn:
        return conn.execute(sql, args).fetchall()


def write_jsonl(root: Path, name: str, rows: list[dict]) -> Path:
    path = root / name
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(body, encoding="utf-8")
    return path


def comment(native_id, text, **extra):
    row = {"platform": "generic", "kind": "comment", "native_id": native_id, "text": text}
    row.update(extra)
    return row


def test_submit_is_accepted_and_example_keeps_text(ctx, project):
    allow(ctx, FIXTURES)
    env = submit(
        ctx,
        request(
            project, [local(FIXTURES / "comments.jsonl", "jsonl")], ["comments"], key="example"
        ),
    )
    assert env.status == "accepted"
    assert env.data["state"] == "queued"
    pinned = env.data["job"]["payload"]["inputs"][0]
    raw = (FIXTURES / "comments.jsonl").read_bytes()
    assert pinned["sha256"] == hashlib.sha256(raw).hexdigest()
    assert pinned["byte_size"] == len(raw)
    assert pinned["path"] == str((FIXTURES / "comments.jsonl").resolve())
    job = finish(ctx, env)
    assert job.state == "succeeded" and job.error is None
    assert job.stop_reason == "input_exhausted"
    assert job.coverage_status == "scope_satisfied"
    assert len(data(job)["rows"]) == 1 and data(job)["rows"][0]["status"] == "committed"
    stored = q(ctx, "SELECT text FROM item_revisions")[0]["text"]
    assert stored == "下次活動是否有字幕？這是自製測試資料。"
    source = q(ctx, "SELECT collection_method, upstream_collection_method FROM sources")[0]
    assert source["collection_method"] == "file_import"
    assert source["upstream_collection_method"] == "synthetic_fixture"
    assert not (ctx.paths.root / "staging" / job.id).exists()


def test_url_is_unsupported_before_a_job_exists(ctx, project):
    before = q(ctx, "SELECT COUNT(*) AS n FROM jobs")[0]["n"]
    missing = submit(
        ctx,
        request(
            "prj_missing_project",
            [m.URLInput(kind="url", url="https://example.invalid/post")],
            ["post"],
            key="remote-missing",
        ),
    )
    present = submit(
        ctx,
        request(
            project,
            [
                m.URLInput(kind="url", url="https://example.invalid/post"),
                m.TextInput(kind="text", text="local"),
            ],
            ["post"],
            key="remote-mixed",
        ),
    )
    for env in (missing, present):
        assert env.status == "error"
        assert env.error.code == m.ErrorCode.UNSUPPORTED
        assert "B12" in env.error.next_action and "B13" in env.error.next_action
        assert "LIVE" in env.error.next_action
    assert q(ctx, "SELECT COUNT(*) AS n FROM jobs")[0]["n"] == before


def test_scope_limits_and_format_are_checked_at_submit(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    target = root / "one.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    bare = submit(ctx, request(project, [local(target, "jsonl")], ["comments"], key="no-root"))
    assert bare.error.code == m.ErrorCode.SCOPE_REQUIRED
    assert "import_roots" in bare.error.next_action
    allow(ctx, root)
    escaped = submit(ctx, request(project, [local(outside, "jsonl")], ["comments"], key="out"))
    assert escaped.error.code == m.ErrorCode.SCOPE_DENIED
    high = dict(LIMITS)
    high["max_items"] = 501
    denied = submit(
        ctx, request(project, [local(target, "jsonl")], ["comments"], key="high", limits=high)
    )
    assert denied.error.code == m.ErrorCode.SCOPE_DENIED
    crowded = submit(
        ctx,
        request(
            project,
            [local(target, "jsonl"), local(target, "jsonl")],
            ["comments"],
            key="crowded",
            limits={**LIMITS, "max_urls": 1},
        ),
    )
    assert crowded.error.code == m.ErrorCode.LIMIT_REACHED
    mismatch = submit(ctx, request(project, [local(target, "vtt")], ["post"], key="mismatch"))
    assert mismatch.error.code == m.ErrorCode.INPUT_INVALID
    language = submit(
        ctx,
        request(project, [local(target, "jsonl")], ["comments"], key="lang", language="zh-Hant"),
    )
    assert language.error.code == m.ErrorCode.INPUT_INVALID
    ctx.limits = ctx.limits.model_copy(update={"text_max_bytes": 4})
    huge = root / "huge.jsonl"
    huge.write_bytes(b'{"platform":"generic"}\n')
    limited = submit(ctx, request(project, [local(huge, "jsonl")], ["comments"], key="huge"))
    assert limited.error.code == m.ErrorCode.LIMIT_REACHED
    assert q(ctx, "SELECT COUNT(*) AS n FROM jobs")[0]["n"] == 0


def test_identity_revisions_and_absence_is_not_deletion(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    text = "同樣的自製留言"
    rows = [
        comment("keep", "甲"),
        comment("drop-me-not", "乙"),
        comment("dup", text),
        comment("dup", text),
        comment("twin-a", text),
        comment("twin-b", text),
        comment("  spaced  ", "空白也是識別"),
        {"platform": "generic", "kind": "comment", "native_id": "", "text": "弱識別"},
        {"platform": "generic", "kind": "comment", "text": "另一則弱識別"},
    ]
    first = root / "first.jsonl"
    first.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    job = run(ctx, request(project, [local(first, "jsonl")], ["comments"], key="identity"))
    statuses = [row["status"] for row in data(job)["rows"]]
    assert statuses.count("committed") == 8
    assert statuses.count("unchanged") == 1
    items = q(ctx, "SELECT id, native_id, identity_confidence FROM items ORDER BY rowid")
    by_native = {row["native_id"]: row for row in items if row["native_id"]}
    assert by_native["dup"]["identity_confidence"] == "reliable"
    assert by_native["twin-a"]["id"] != by_native["twin-b"]["id"]
    assert by_native["  spaced  "]["identity_confidence"] == "reliable"
    weak = [row for row in items if row["identity_confidence"] == "weak"]
    assert len(weak) == 2
    assert all(
        row["id"].startswith("itm_") and row["id"] != m.payload_digest({"text": "弱識別"})
        for row in weak
    )
    assert {row["native_id"] for row in weak} == {None}
    revisions = q(
        ctx, "SELECT revision FROM item_revisions WHERE item_id=?", (by_native["dup"]["id"],)
    )
    assert [row["revision"] for row in revisions] == [1]

    changed = write_jsonl(
        root, "second.jsonl", [comment("dup", text + "修訂"), comment("keep", "甲")]
    )
    again = run(ctx, request(project, [local(changed, "jsonl")], ["comments"], key="identity-2"))
    assert [row["status"] for row in data(again)["rows"]] == ["committed", "unchanged"]
    dup_revs = q(
        ctx,
        "SELECT revision, text FROM item_revisions WHERE item_id=? ORDER BY revision",
        (by_native["dup"]["id"],),
    )
    assert [row["text"] for row in dup_revs] == [text, text + "修訂"]
    remaining = {row["native_id"] for row in q(ctx, "SELECT native_id FROM items")}
    assert "drop-me-not" in remaining and "keep" in remaining
    assert (
        q(
            ctx,
            "SELECT text FROM item_revisions WHERE item_id=?",
            (by_native["drop-me-not"]["id"],),
        )[0]["text"]
        == "乙"
    )


def test_same_batch_kind_conflict_keeps_the_first_row(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = write_jsonl(
        root,
        "kind.jsonl",
        [
            comment("x", "甲"),
            {"platform": "generic", "kind": "post", "native_id": "x", "text": "乙"},
        ],
    )
    job = run(ctx, request(project, [local(path, "jsonl")], ["post", "comments"], key="kind"))
    assert [row["status"] for row in data(job)["rows"]] == ["committed", "rejected"]
    assert data(job)["rows"][1]["field"] == "kind"
    assert data(job)["rows"][1]["code"] == "E_REFERENCE_INVALID"
    assert job.state == "partial" and job.stop_reason == "row_errors"
    stored = q(ctx, "SELECT kind FROM items")
    assert [row["kind"] for row in stored] == ["comment"]
    assert q(ctx, "SELECT text FROM item_revisions")[0]["text"] == "甲"


def test_parents_resolve_in_any_order_and_stay_inside_the_project(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = write_jsonl(
        root,
        "parents.jsonl",
        [
            comment("child", "子", parent_native_id="parent"),
            comment("self", "自己", parent_native_id="self"),
            comment("a", "甲", parent_native_id="b"),
            comment("b", "乙", parent_native_id="a"),
            comment("parent", "父"),
            comment("room-child", "別室", native_scope="room-b", parent_native_id="parent"),
        ],
    )
    job = run(ctx, request(project, [local(path, "jsonl")], ["comments"], key="parents"))
    by_status = {row["row"]: row["status"] for row in data(job)["rows"]}
    assert by_status[1] == "committed"
    assert by_status[2] == "rejected" and data(job)["rows"][1]["field"] == "parent_native_id"
    assert by_status[3] == "committed"
    assert by_status[4] == "rejected"
    items = {row["native_id"]: row for row in q(ctx, "SELECT * FROM items")}
    assert "self" not in items and "b" not in items
    assert items["a"]["unresolved_parent"] == "b" and items["a"]["parent_id"] is None
    assert items["child"]["parent_id"] == items["parent"]["id"]
    assert items["child"]["unresolved_parent"] is None
    assert items["room-child"]["unresolved_parent"] == "parent"
    assert items["room-child"]["parent_id"] is None

    other = app.create_project(ctx, {"name": "Other", "request_key": "other"}).data["id"]
    foreign = write_jsonl(
        root, "foreign.jsonl", [comment("child", "別案", parent_native_id="parent")]
    )
    run(ctx, request(other, [local(foreign, "jsonl")], ["comments"], key="foreign"))
    foreign_child = q(
        ctx, "SELECT * FROM items WHERE project_id=? AND native_id='child'", (other,)
    )[0]
    assert foreign_child["parent_id"] is None
    assert foreign_child["unresolved_parent"] == "parent"
    assert foreign_child["parent_id"] != items["parent"]["id"]

    later = write_jsonl(root, "later.jsonl", [comment("b", "乙回來了")])
    run(ctx, request(project, [local(later, "jsonl")], ["comments"], key="later-parent"))
    resolved = q(ctx, "SELECT parent_id, unresolved_parent FROM items WHERE native_id='a'")[0]
    parent = q(ctx, "SELECT id FROM items WHERE native_id='b' AND project_id=?", (project,))[0]
    assert resolved["parent_id"] == parent["id"] and resolved["unresolved_parent"] is None
    # The reverse edge was rejected, so the returned parent does not point at a.
    returned = q(ctx, "SELECT parent_id FROM items WHERE id=?", (parent["id"],))[0]
    assert returned["parent_id"] is None


def test_parent_field_updates_without_rewriting_identical_text(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = write_jsonl(
        root,
        "link.jsonl",
        [
            comment("c", "甲"),
            comment("p", "父"),
            comment("c", "甲", parent_native_id="p"),
        ],
    )
    job = run(ctx, request(project, [local(path, "jsonl")], ["comments"], key="link"))
    assert [row["status"] for row in data(job)["rows"]] == ["committed", "committed", "committed"]
    child = q(ctx, "SELECT id, parent_id FROM items WHERE native_id='c'")[0]
    parent = q(ctx, "SELECT id FROM items WHERE native_id='p'")[0]
    assert child["parent_id"] == parent["id"]
    assert [
        row["revision"]
        for row in q(ctx, "SELECT revision FROM item_revisions WHERE item_id=?", (child["id"],))
    ] == [1]
    absent = write_jsonl(root, "absent.jsonl", [comment("c", "甲")])
    run(ctx, request(project, [local(absent, "jsonl")], ["comments"], key="absent"))
    assert (
        q(ctx, "SELECT parent_id FROM items WHERE id=?", (child["id"],))[0]["parent_id"]
        == parent["id"]
    )
    cleared = write_jsonl(root, "clear.jsonl", [comment("c", "甲", parent_native_id=None)])
    run(ctx, request(project, [local(cleared, "jsonl")], ["comments"], key="clear"))
    cleared_row = q(
        ctx, "SELECT parent_id, unresolved_parent FROM items WHERE id=?", (child["id"],)
    )[0]
    assert cleared_row["parent_id"] is None and cleared_row["unresolved_parent"] is None


def test_provenance_keeps_method_and_upstream_apart(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = write_jsonl(
        root,
        "prov.jsonl",
        [
            comment(
                "n",
                "甲",
                upstream_collection_method="synthetic_fixture",
                source_url="https://example.org/a",
            )
        ],
    )
    run(
        ctx,
        request(
            project,
            [local(path, "jsonl")],
            ["comments"],
            key="prov",
            access_grant_id="grant_local_ignored",
        ),
    )
    source = q(ctx, "SELECT * FROM sources WHERE native_id='n'")[0]
    assert source["collection_method"] == "file_import"
    assert source["upstream_collection_method"] == "synthetic_fixture"
    assert source["original_url"] == "https://example.org/a"
    same = write_jsonl(root, "same.jsonl", [comment("n", "甲", source_url="https://example.org/b")])
    run(ctx, request(project, [local(same, "jsonl")], ["comments"], key="prov-same"))
    kept = q(ctx, "SELECT * FROM sources WHERE native_id='n'")[0]
    assert kept["upstream_collection_method"] == "synthetic_fixture"
    assert kept["collection_method"] == "file_import"
    assert kept["original_url"] == "https://example.org/a"
    manual = write_jsonl(
        root,
        "manual.jsonl",
        [comment("n", "乙", upstream_collection_method="manual")],
    )
    run(ctx, request(project, [local(manual, "jsonl")], ["comments"], key="prov-manual"))
    updated = q(ctx, "SELECT * FROM sources WHERE native_id='n'")[0]
    assert updated["collection_method"] == "file_import"
    # A known upstream stays; this run still records the declaration on the new revision.
    assert updated["upstream_collection_method"] == "synthetic_fixture"
    evidence = json.loads(
        q(ctx, "SELECT evidence FROM item_revisions WHERE revision=2")[0]["evidence"]
    )
    assert evidence["collection_method"] == "file_import"
    assert evidence["upstream_collection_method"] == "manual"
    assert SENTINEL not in json.dumps(evidence)
    opened = write_jsonl(root, "opened.jsonl", [comment("open", "丙")])
    run(ctx, request(project, [local(opened, "jsonl")], ["comments"], key="prov-open"))
    assert (
        q(ctx, "SELECT upstream_collection_method FROM sources WHERE native_id='open'")[0][
            "upstream_collection_method"
        ]
        == "unknown"
    )
    filled = write_jsonl(
        root,
        "filled.jsonl",
        [comment("open", "丁", upstream_collection_method="manual")],
    )
    run(ctx, request(project, [local(filled, "jsonl")], ["comments"], key="prov-filled"))
    filled_source = q(ctx, "SELECT * FROM sources WHERE native_id='open'")[0]
    # Acquisition columns are immutable. The later declaration is a new revision.
    assert filled_source["upstream_collection_method"] == "unknown"
    assert filled_source["collection_method"] == "file_import"
    filled_evidence = json.loads(
        q(
            ctx,
            "SELECT r.evidence FROM item_revisions r JOIN items i ON i.id=r.item_id "
            "WHERE i.native_id='open' AND r.revision=2",
        )[0]["evidence"]
    )
    assert filled_evidence["collection_method"] == "file_import"
    assert filled_evidence["upstream_collection_method"] == "manual"

    typed = run(
        ctx,
        request(project, [m.TextInput(kind="text", text="純文字輸入")], ["post"], key="typed"),
    )
    assert data(typed)["rows"][0]["status"] == "committed"
    typed_source = q(ctx, "SELECT * FROM sources WHERE collection_method='text_input'")[0]
    assert typed_source["upstream_collection_method"] == "unknown"
    assert typed_source["native_id"] is None
    assert (
        q(
            ctx,
            "SELECT text FROM item_revisions WHERE item_id=?",
            (data(typed)["rows"][0]["item_id"],),
        )[0]["text"]
        == "純文字輸入"
    )


def test_configured_utf8_limit_rejects_oversize_text(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = ctx.paths.root / "metamuse.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("text_max_bytes = 20971520", "text_max_bytes = 4"),
        encoding="utf-8",
    )
    config = app.Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    ctx.policy = config
    ctx.limits = config.limits
    file = write_jsonl(root, "wide.jsonl", [comment("n", "繁中")])
    env = submit(ctx, request(project, [local(file, "jsonl")], ["comments"], key="utf8-cap"))
    assert env.status == "error" and env.error.code == m.ErrorCode.LIMIT_REACHED
    assert q(ctx, "SELECT COUNT(*) AS n FROM jobs")[0]["n"] == 0
    parsed = collect.parse_jsonl(file.read_bytes(), text_max_bytes=ctx.limits.text_max_bytes)
    assert parsed[0]["status"] == "rejected"
    assert parsed[0]["field"] == "text"
    assert parsed[0]["code"] == "E_LIMIT_REACHED"
    assert parsed[0]["message"] == "text exceeds the UTF-8 byte limit"


def test_exact_text_null_empty_time_and_unicode(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    body = "繁中\r\n👨‍👩‍👧‍👦🇹🇼"
    path = write_jsonl(
        root,
        "unicode.jsonl",
        [
            comment("u", body, created_at="2020-01-02T03:04:05+08:00", author_display="測試者"),
            {"platform": "generic", "kind": "post", "native_id": "empty", "text": ""},
            {"platform": "generic", "kind": "post", "native_id": "none", "text": None},
            comment("raw", "有原文時間", created_at_raw="昨天"),
            comment("naive", "無時區", created_at="2020-01-01T00:00:00"),
        ],
    )
    # JSONL resources include posts and comments.
    job = run(ctx, request(project, [local(path, "jsonl")], ["post", "comments"], key="unicode"))
    assert job.state == "partial"
    naive = next(row for row in data(job)["rows"] if row["row"] == 5)
    assert naive["status"] == "rejected" and naive["field"] == "created_at"
    stored = q(
        ctx,
        "SELECT r.text, r.raw_time, r.parsed_time, r.text_hash, r.missing_fields, r.evidence, "
        "i.native_id "
        "FROM item_revisions r JOIN items i ON i.id=r.item_id",
    )
    by_id = {row["native_id"]: row for row in stored}
    assert by_id["u"]["text"] == body
    assert by_id["u"]["raw_time"] == "2020-01-02T03:04:05+08:00"
    assert by_id["u"]["parsed_time"] == "2020-01-01T19:04:05+00:00"
    assert json.loads(by_id["u"]["evidence"])["author_display"] == "測試者"
    assert by_id["empty"]["text"] == ""
    assert by_id["empty"]["text_hash"] == m.payload_digest({"text": ""})
    assert json.loads(by_id["empty"]["missing_fields"]) == []
    assert by_id["none"]["text"] == ""
    assert by_id["none"]["text_hash"] == m.payload_digest({"text": None})
    assert json.loads(by_id["none"]["missing_fields"]) == ["text"]
    assert by_id["empty"]["text_hash"] != by_id["none"]["text_hash"]
    assert by_id["raw"]["raw_time"] == "昨天" and by_id["raw"]["parsed_time"] is None
    plain = root / "plain.txt"
    plain.write_bytes(b"\xef\xbb\xbf\xe7\xb9\x81\xe4\xb8\xad\r\n\xef\xbb\xbf")
    typed = run(ctx, request(project, [local(plain, "text")], ["post"], key="plain"))
    assert data(typed)["rows"][0]["status"] == "committed"
    assert (
        q(
            ctx,
            "SELECT text FROM item_revisions WHERE item_id=?",
            (data(typed)["rows"][0]["item_id"],),
        )[0]["text"]
        == "繁中\r\n\ufeff"
    )
    events = q(ctx, "SELECT detail FROM events")
    assert all(body not in row["detail"] and SENTINEL not in row["detail"] for row in events)


def test_row_errors_commit_good_rows_and_parser_failure_is_not_empty(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    good = {"platform": "generic", "kind": "comment", "native_id": "ok", "text": "保留"}
    raw = json.dumps(good, ensure_ascii=False).encode() + b"\n"
    raw += b'{"platform":"generic","kind":"comment","native_id":"bad","text":"\xff"}\n'
    raw += b"\n"
    raw += b"{not json}\n"
    raw += (
        json.dumps({"platform": "generic", "kind": "note", "native_id": "k", "text": "x"}).encode()
        + b"\n"
    )
    raw += json.dumps({"platform": 1, "kind": "comment", "text": "x"}).encode() + b"\n"
    raw += json.dumps({"kind": "comment", "text": "x"}).encode() + b"\n"
    raw += json.dumps({"platform": "p" * 129, "kind": "comment", "text": "x"}).encode() + b"\n"
    raw += (
        json.dumps({"platform": "generic", "kind": "comment", "text": "a" * 1_000_001}).encode()
        + b"\n"
    )
    raw += b'{"platform":"generic","kind":"comment","text":NaN}\n'
    raw += b'{"platform":"generic","kind":"comment","text":Infinity}\n'
    raw += b'{"platform":"generic","platform":"other","kind":"comment"}\n'
    raw += (
        json.dumps({"platform": "generic", "kind": "comment", "text": "x", "extra": 1}).encode()
        + b"\n"
    )
    path = root / "mixed.jsonl"
    path.write_bytes(raw)
    job = run(ctx, request(project, [local(path, "jsonl")], ["comments"], key="mixed"))
    assert job.state == "partial" and job.stop_reason == "row_errors" and job.error is None
    rows = data(job)["rows"]
    assert rows[0]["status"] == "committed" and rows[0]["line"] == 1
    rejected = [row for row in rows if row["status"] == "rejected"]
    assert {row["line"] for row in rejected} >= {2, 4, 5, 6, 7, 8, 9, 10, 11, 12}
    assert any(row["line"] == 2 and row["code"] == "E_INPUT_INVALID" for row in rejected)
    assert any(row["field"] == "kind" and row["line"] == 5 for row in rejected)
    assert any(row["field"] == "platform" and row["code"] == "E_LIMIT_REACHED" for row in rejected)
    assert any(row["field"] == "text" and row["code"] == "E_LIMIT_REACHED" for row in rejected)
    assert any(row["field"] == "extra" for row in rejected)
    assert q(ctx, "SELECT COUNT(*) AS n FROM items")[0]["n"] == 1
    assert SENTINEL not in json.dumps(job.result)

    array = root / "nan.json"
    array.write_text('[{"platform":"generic","kind":"post","text":NaN}]\n', encoding="utf-8")
    failed = run(ctx, request(project, [local(array, "json")], ["post"], key="nan-doc"))
    assert failed.state == "partial" and failed.stop_reason == "parser_failed"
    assert failed.coverage_status == "partial"
    assert data(failed)["rows"] == []
    assert data(failed)["inputs"][0]["code"] == "E_INPUT_INVALID"
    assert q(ctx, "SELECT COUNT(*) AS n FROM items")[0]["n"] == 1

    empty = root / "empty.json"
    empty.write_text("[]\n", encoding="utf-8")
    blank = run(ctx, request(project, [local(empty, "json")], ["post"], key="empty"))
    assert blank.state == "succeeded" and blank.stop_reason == "empty_input"
    assert blank.coverage_status == "bounded"

    header = root / "header.csv"
    header.write_text("platform,kind\n", encoding="utf-8")
    csv_empty = run(ctx, request(project, [local(header, "csv")], ["comments"], key="csv-empty"))
    assert csv_empty.stop_reason == "empty_input" and csv_empty.state == "succeeded"
    broken = root / "broken.csv"
    broken.write_text("name,note\n", encoding="utf-8")
    csv_bad = run(ctx, request(project, [local(broken, "csv")], ["comments"], key="csv-bad"))
    assert csv_bad.stop_reason == "parser_failed" and csv_bad.state == "partial"


def test_csv_mapping_and_plain_text_filters(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = root / "rows.csv"
    path.write_bytes(
        "platform,kind,native_id,native_scope,parent_native_id,text,source_url,"
        "upstream_collection_method,created_at_raw,created_at,author_display,media_ref,media_kind\r\n"
        'generic,comment,c1,,p1,"線一\r\n線二",https://example.org/c,synthetic_fixture,昨天,,'
        "作者,,\r\n"
        "generic,post,p1,,,父文,,,,,,,\r\n".encode()
    )
    job = run(ctx, request(project, [local(path, "csv")], ["post", "comments"], key="csv"))
    assert [row["status"] for row in data(job)["rows"]] == ["committed", "committed"]
    child = q(
        ctx,
        "SELECT i.parent_id, r.text FROM items i JOIN item_revisions r ON r.item_id=i.id "
        "WHERE i.native_id='c1'",
    )[0]
    parent = q(ctx, "SELECT id FROM items WHERE native_id='p1'")[0]
    assert child["text"] == "線一\r\n線二"
    assert child["parent_id"] == parent["id"]
    source = q(
        ctx, "SELECT upstream_collection_method, original_url FROM sources WHERE native_id='c1'"
    )[0]
    assert source["upstream_collection_method"] == "synthetic_fixture"
    assert source["original_url"] == "https://example.org/c"
    blank_parent = root / "blank.csv"
    blank_parent.write_text(
        "platform,kind,native_id,parent_native_id,text\ngeneric,comment,c1,,線一\n",
        encoding="utf-8",
    )
    run(ctx, request(project, [local(blank_parent, "csv")], ["comments"], key="csv-blank"))
    assert q(ctx, "SELECT parent_id FROM items WHERE native_id='c1'")[0]["parent_id"] is None

    posts = root / "posts.jsonl"
    posts.write_text(json.dumps(comment("only-comment", "略過")) + "\n", encoding="utf-8")
    skipped = run(ctx, request(project, [local(posts, "jsonl")], ["post"], key="skip"))
    assert data(skipped)["rows"][0]["status"] == "skipped"
    assert skipped.stop_reason == "input_exhausted"
    assert q(ctx, "SELECT COUNT(*) AS n FROM items WHERE native_id='only-comment'")[0]["n"] == 0


def test_max_items_stops_with_honest_coverage(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    rows = [comment(f"n{i}", f"第{i}") for i in range(3)]
    path = write_jsonl(root, "cap.jsonl", rows)
    capped = run(
        ctx,
        request(
            project,
            [local(path, "jsonl")],
            ["comments"],
            key="cap",
            limits={**LIMITS, "max_items": 2},
        ),
    )
    assert capped.state == "succeeded" and capped.stop_reason == "limit_reached"
    assert capped.coverage_status == "bounded"
    assert [row["status"] for row in data(capped)["rows"]] == ["committed", "committed"]
    assert q(ctx, "SELECT COUNT(*) AS n FROM items")[0]["n"] == 2
    exact = write_jsonl(root, "exact.jsonl", rows[:2])
    full = run(
        ctx,
        request(
            project,
            [local(exact, "jsonl")],
            ["comments"],
            key="exact",
            limits={**LIMITS, "max_items": 2},
        ),
    )
    assert full.stop_reason == "input_exhausted" and full.coverage_status == "scope_satisfied"


def test_changed_bytes_record_data_stale_without_failing_the_job(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    first = write_jsonl(root, "a.jsonl", [comment("a", "甲")])
    second = write_jsonl(root, "b.jsonl", [comment("b", "乙")])
    env = submit(
        ctx,
        request(
            project, [local(first, "jsonl"), local(second, "jsonl")], ["comments"], key="stale"
        ),
    )
    first.write_text(json.dumps(comment("a", "改了")) + "\n", encoding="utf-8")
    job = finish(ctx, env)
    assert job.state == "partial" and job.error is None and job.state != "awaiting_user"
    assert job.stop_reason == "data_stale"
    assert data(job)["inputs"][0]["code"] == "E_DATA_STALE"
    assert [row["status"] for row in data(job)["rows"]] == ["committed"]
    assert q(ctx, "SELECT native_id FROM items")[0]["native_id"] == "b"
    gone = write_jsonl(root, "gone.jsonl", [comment("c", "丙")])
    env = submit(ctx, request(project, [local(gone, "jsonl")], ["comments"], key="gone"))
    gone.unlink()
    missing = finish(ctx, env)
    assert missing.stop_reason == "data_stale"
    assert data(missing)["inputs"][0]["code"] == "E_DATA_STALE"
    narrowed = write_jsonl(root, "narrow.jsonl", [comment("d", "丁")])
    env = submit(ctx, request(project, [local(narrowed, "jsonl")], ["comments"], key="narrow"))
    other = tmp_path / "elsewhere"
    other.mkdir()
    allow(ctx, other)
    denied = finish(ctx, env)
    assert data(denied)["inputs"][0]["code"] == "E_SCOPE_DENIED"
    assert denied.state == "partial" and denied.error is None


def test_restart_commits_each_accepted_row_once(ctx, project, tmp_path, monkeypatch):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    rows = [comment(f"n{index}", f"第{index}則{SENTINEL}") for index in range(5)]
    path = write_jsonl(root, "batches.jsonl", rows)
    monkeypatch.setattr(collect, "BATCH_SIZE", 2)
    seen: list[str] = []
    original = collect._Book.apply

    def spy(self, record, **kwargs):
        seen.append(record.native_id)
        return original(self, record, **kwargs)

    monkeypatch.setattr(collect._Book, "apply", spy)
    calls = {"n": 0}

    def crash_once(step):
        del step
        calls["n"] += 1
        if calls["n"] == 1:
            raise SystemExit(7)

    monkeypatch.setattr(collect, "_note_batch", crash_once)
    env = submit(ctx, request(project, [local(path, "jsonl")], ["comments"], key="restart"))
    with pytest.raises(SystemExit):
        with jobs.Executor(ctx.store) as executor:
            executor.run_once()
    assert ctx.store.get(m.Job, env.data["job_id"]).state == "running"
    assert q(ctx, "SELECT COUNT(*) AS n FROM items")[0]["n"] == 2
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert job.state == "succeeded"
    assert seen == [f"n{index}" for index in range(5)]
    assert q(ctx, "SELECT COUNT(*) AS n FROM items")[0]["n"] == 5
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_revisions")[0]["n"] == 5
    details = [
        row["detail"] for row in q(ctx, "SELECT detail FROM events WHERE kind='collect.batch'")
    ]
    assert details and all(SENTINEL not in detail for detail in details)


def test_replay_and_idempotency(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = write_jsonl(root, "once.jsonl", [comment("n", "甲")])
    first = submit(ctx, request(project, [local(path, "jsonl")], ["comments"], key="once"))
    second = submit(ctx, request(project, [local(path, "jsonl")], ["comments"], key="once"))
    assert first.status == second.status == "accepted"
    assert first.data["job_id"] == second.data["job_id"]
    path.write_text(json.dumps(comment("n", "乙")) + "\n", encoding="utf-8")
    conflict = submit(ctx, request(project, [local(path, "jsonl")], ["comments"], key="once"))
    assert conflict.error.code == m.ErrorCode.IDEMPOTENCY_CONFLICT
    assert q(ctx, "SELECT COUNT(*) AS n FROM jobs")[0]["n"] == 1


def test_staging_sweep_drops_only_owned_terminal_jobs(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = write_jsonl(root, "sweep.jsonl", [comment("n", "甲")])
    job = run(ctx, request(project, [local(path, "jsonl")], ["comments"], key="sweep"))
    staging = ctx.paths.root / "staging"
    finished = staging / job.id
    finished.mkdir(parents=True)
    (finished / "orphan").write_text("x", encoding="utf-8")
    # The older queued job is the one run_once claims. The newer one stays queued.
    runner = submit(ctx, request(project, [local(path, "jsonl")], ["comments"], key="sweep-run"))
    queued = submit(ctx, request(project, [local(path, "jsonl")], ["comments"], key="queued-sweep"))
    queued_dir = staging / queued.data["job_id"]
    queued_dir.mkdir()
    (queued_dir / "keep").write_text("queued", encoding="utf-8")
    (staging / "object-temp").write_text("core", encoding="utf-8")
    (staging / "keep-dir").mkdir()
    outside = tmp_path / "outside-staging"
    outside.mkdir()
    (outside / "secret").write_text("secret", encoding="utf-8")
    link = staging / "job_link"
    link.symlink_to(outside, target_is_directory=True)
    (staging / "job_orphan").mkdir()
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    assert ctx.store.get(m.Job, runner.data["job_id"]).state == "succeeded"
    assert not finished.exists()
    assert not (staging / "job_orphan").exists()
    assert (queued_dir / "keep").read_text(encoding="utf-8") == "queued"
    assert (staging / "object-temp").read_text(encoding="utf-8") == "core"
    assert (staging / "keep-dir").is_dir()
    assert link.is_symlink()
    assert (outside / "secret").read_text(encoding="utf-8") == "secret"


def test_capability_is_fixture_passed_and_registration_is_idempotent():
    collect.register_operations()
    collect.register_operations()
    operation = jobs.OPERATIONS["import.local"]
    assert operation.side_effect_class == "none"
    assert operation.request_model is m.CollectRequest
    status = collect.capability_status(None)["metamuse_collect"]
    assert status == {
        "verification": "fixture_passed",
        "environment": "ready",
        "permission": "granted_scope",
    }


def test_json_array_object_and_resource_skip_do_not_invent_rows(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    array = root / "array.json"
    array.write_text(
        json.dumps(
            [
                comment("good", "好"),
                {"platform": "generic", "kind": "comment", "text": 5},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    job = run(ctx, request(project, [local(array, "json")], ["comments"], key="array"))
    assert [row["status"] for row in data(job)["rows"]] == ["committed", "rejected"]
    assert data(job)["rows"][1]["field"] == "text"
    single = root / "one.json"
    single.write_text(json.dumps(comment("solo", "單筆")), encoding="utf-8")
    solo = run(ctx, request(project, [local(single, "json")], ["comments"], key="solo"))
    assert data(solo)["rows"][0]["row"] == 1 and solo.state == "succeeded"
    empty_text = root / "empty.txt"
    empty_text.write_bytes(b"")
    plain = run(ctx, request(project, [local(empty_text, "text")], ["post"], key="empty-text"))
    assert data(plain)["rows"][0]["status"] == "committed"
    assert (
        q(
            ctx,
            "SELECT text FROM item_revisions WHERE item_id=?",
            (data(plain)["rows"][0]["item_id"],),
        )[0]["text"]
        == ""
    )
