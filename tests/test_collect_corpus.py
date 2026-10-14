"""Import the shared synthetic corpus in place (B05/B06; AT-002..007, 013, 017, 031, 041)."""

import csv
import hashlib
import io
import json
import tomllib
from pathlib import Path

import pytest

from metamuse import app, jobs, safety
from metamuse import models as m

CORPUS = Path(__file__).parent / "fixtures" / "corpus"
LIMITS = {
    "max_urls": 10,
    "max_items": 500,
    "max_bytes": 1_073_741_824,
    "wall_clock_seconds": 900,
}


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


def manifest() -> dict:
    return json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


def q(ctx, sql, args=()):
    with ctx.store.connection() as conn:
        return conn.execute(sql, args).fetchall()


def payload(job):
    return job.result["data"]["steps"][0]["data"]


def run_files(ctx, project, inputs, resources, key):
    request = m.CollectRequest(
        project_id=project,
        inputs=inputs,
        resources=resources,
        limits=m.CollectLimits(**LIMITS),
        access_grant_id=None,
        request_key=key,
    )
    env = app.submit_collect(ctx, request)
    assert env.status == "accepted", env.error
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    return ctx.store.get(m.Job, env.data["job_id"])


def run_file(ctx, project, path: Path, fmt: str, resources, key):
    item = m.LocalFileInput(kind="local_file", path=str(path.resolve()), format=fmt)
    return run_files(ctx, project, [item], resources, key)


def project_for(ctx, key: str) -> str:
    return app.create_project(ctx, {"name": key, "request_key": key}).data["id"]


def jsonl_records(path: Path) -> list[dict]:
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        record["_line"] = number
        records.append(record)
    return records


def stored_items(ctx, project: str) -> dict[str, dict]:
    rows = q(ctx, "SELECT * FROM items WHERE project_id=?", (project,))
    return {row["native_id"]: row for row in rows if row["native_id"]}


def test_corpus_text_fidelity(ctx):
    allow(ctx, CORPUS)
    files = manifest()["files"]
    zh_path = CORPUS / "import/posts_zh.jsonl"
    zh = project_for(ctx, "zh")
    job = run_file(ctx, zh, zh_path, "jsonl", ["post"], "zh")
    assert files["import/posts_zh.jsonl"]["expected"]["valid_rows"] == 10
    assert job.state == "partial"  # three refs point beside the jsonl and do not resolve
    for record in jsonl_records(zh_path):
        item = stored_items(ctx, zh)[record["native_id"]]
        revision = ctx.store.get(m.ItemRevision, item["id"])
        assert revision.text == record["text"]
        source = q(ctx, "SELECT * FROM sources WHERE id=?", (item["source_id"],))[0]
        assert source["collection_method"] == "file_import"
        assert source["upstream_collection_method"] == record["upstream_collection_method"]
        assert source["upstream_collection_method"] != "manual"
    joined = "\n".join(record["text"] for record in jsonl_records(zh_path))
    assert "👨\u200d💻" in joined and "🇹🇼" in joined and "\n" in joined

    array_path = CORPUS / "import/posts.json"
    array_project = project_for(ctx, "json-array")
    array_job = run_file(ctx, array_project, array_path, "json", ["post"], "json-array")
    array = json.loads(array_path.read_text(encoding="utf-8"))
    expected = files["import/posts.json"]["expected"]
    assert expected["total_rows"] == len(array) == 5
    assert len(stored_items(ctx, array_project)) == expected["valid_rows"]
    for record in array:
        item_id = stored_items(ctx, array_project)[record["native_id"]]["id"]
        assert ctx.store.get(m.ItemRevision, item_id).text == record["text"]
    assert array_job.coverage_status == "partial"

    for name, key in (("posts.csv", "csv"), ("posts_bom.csv", "bom")):
        path = CORPUS / "import" / name
        meta = files[f"import/{name}"]["expected"]
        assert path.read_bytes().startswith(b"\xef\xbb\xbf") is meta["has_bom"]
        assert meta["includes_formula_injection_test"] is True
        owner = project_for(ctx, key)
        run_file(ctx, owner, path, "csv", ["post"], key)
        decoded = path.read_bytes().decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(decoded, newline="")))
        assert len(rows) == meta["total_rows"] == 6
        assert "\n" in rows[0]["text"]
        for record in rows:
            item_id = stored_items(ctx, owner)[record["native_id"]]["id"]
            assert ctx.store.get(m.ItemRevision, item_id).text == record["text"]
        formula = stored_items(ctx, owner)["post-csv-formula-01"]
        formula_text = ctx.store.get(m.ItemRevision, formula["id"]).text
        assert formula_text.startswith("=SUM(")
        assert formula_text == rows[-1]["text"]

    notes = CORPUS / "import/notes.txt"
    note_meta = files["import/notes.txt"]["expected"]
    assert note_meta["format"] == "plain_text"
    assert len(notes.read_text(encoding="utf-8").splitlines()) == note_meta["lines"]
    note_project = project_for(ctx, "notes")
    note_job = run_file(ctx, note_project, notes, "text", ["post"], "notes")
    assert note_job.state == "succeeded" and note_job.stop_reason == "input_exhausted"
    note_items = q(ctx, "SELECT * FROM items WHERE project_id=?", (note_project,))
    assert len(note_items) == 1
    assert note_items[0]["identity_confidence"] == "weak"
    assert note_items[0]["kind"] == "post"
    note_text = ctx.store.get(m.ItemRevision, note_items[0]["id"]).text
    assert note_text == notes.read_text(encoding="utf-8")


def test_corpus_mixed_bad_rows(ctx, project):
    allow(ctx, CORPUS)
    path = CORPUS / "import/comments_mixed_bad.jsonl"
    expected = manifest()["files"]["import/comments_mixed_bad.jsonl"]["expected"]
    job = run_file(ctx, project, path, "jsonl", ["comments"], "mixed")
    rows = payload(job)["rows"]
    assert len(rows) == expected["total_rows"]
    assert job.state == "partial" and job.stop_reason == "row_errors"
    by_line = {row["line"]: row for row in rows}
    invalid = {spec["line"]: spec["error_code"] for spec in expected["invalid_rows"]}
    assert len(invalid) == expected["total_rows"] - expected["valid_rows"]
    committed = 0
    for row in rows:
        if row["line"] in invalid:
            assert row["status"] == "rejected"
            assert row["code"] == invalid[row["line"]]
        else:
            assert row["status"] == "committed" and row.get("code") is None
            committed += 1
    assert committed == expected["valid_rows"] == 7
    assert by_line[6]["field"] == "kind"
    assert by_line[10]["field"] == "text" and by_line[10]["code"] == "E_LIMIT_REACHED"


def test_corpus_reply_tree_and_cross_project(ctx, project):
    allow(ctx, CORPUS)
    path = CORPUS / "import/comments_tree.jsonl"
    expected = manifest()["files"]["import/comments_tree.jsonl"]["expected"]
    records = jsonl_records(path)
    assert len(records) == expected["total_rows"] == expected["valid_rows"]
    job = run_file(ctx, project, path, "jsonl", ["post", "comments"], "tree")
    assert job.state == "partial" and job.stop_reason == "row_errors"
    by_line = {row["line"]: row for row in payload(job)["rows"]}
    items = stored_items(ctx, project)
    for native, spec in expected["parent_resolutions"].items():
        if spec["status"] == "weak_identity_created":
            continue
        matches = [record for record in records if record.get("native_id") == native]
        assert matches
        if spec["status"].startswith("rejected"):
            assert native not in items
            for record in matches:
                outcome = by_line[record["_line"]]
                assert outcome["status"] == "rejected"
                assert outcome["code"] == spec["error_code"]
                assert outcome["field"] == "parent_native_id"
            continue
        item = items[native]
        if spec["status"] == "resolved":
            assert item["unresolved_parent"] is None
            parent = spec["expected_parent"]
            if parent is None:
                assert item["parent_id"] is None
            else:
                assert item["parent_id"] == items[parent]["id"]
        else:
            assert spec["status"] == "unresolved"
            assert item["parent_id"] is None
            assert item["unresolved_parent"] == matches[0]["parent_native_id"]

    identical = [
        by_line[record["_line"]]
        for record in records
        if record.get("native_id") == "c-dup-identical"
    ]
    assert [row["status"] for row in identical] == ["committed", "unchanged"]
    same = ctx.store.get(m.ItemRevision, items["c-dup-identical"]["id"])
    assert same.revision == 1

    revised_rows = [record for record in records if record.get("native_id") == "c-dup-revision"]
    revised = [by_line[record["_line"]] for record in revised_rows]
    assert [row["status"] for row in revised] == ["committed", "committed"]
    assert [row["revision"] for row in revised] == [1, 2]
    history = q(
        ctx,
        "SELECT revision, text FROM item_revisions WHERE item_id=? ORDER BY revision",
        (items["c-dup-revision"]["id"],),
    )
    assert [row["text"] for row in history] == [record["text"] for record in revised_rows]

    weak = q(ctx, "SELECT * FROM items WHERE project_id=? AND native_id IS NULL", (project,))
    assert len(weak) == 2
    root = items["post-tree-root"]["id"]
    assert all(row["identity_confidence"] == "weak" and row["parent_id"] == root for row in weak)
    assert all(row["id"].startswith("itm_") for row in weak)
    left = ctx.store.get(m.ItemRevision, items["c-same-text-01"]["id"])
    right = ctx.store.get(m.ItemRevision, items["c-same-text-02"]["id"])
    assert left.text == right.text
    assert items["c-same-text-01"]["id"] != items["c-same-text-02"]["id"]

    other = project_for(ctx, "tree-b")
    run_file(ctx, other, path, "jsonl", ["post", "comments"], "tree-b")
    crossed = q(
        ctx,
        "SELECT c.id FROM items c JOIN items p ON c.parent_id=p.id "
        "WHERE c.project_id!=p.project_id",
    )
    assert crossed == []
    foreign = stored_items(ctx, other)["c-tree-valid-1"]
    assert foreign["id"] != items["c-tree-valid-1"]["id"]
    parent_project = q(ctx, "SELECT project_id FROM items WHERE id=?", (foreign["parent_id"],))
    assert parent_project[0]["project_id"] == other

    rejected = manifest()["files"]["import/project_b_comments.jsonl"]["expected"]
    third = project_for(ctx, "project-b-file")
    bad = run_file(
        ctx,
        third,
        CORPUS / "import/project_b_comments.jsonl",
        "jsonl",
        ["post", "comments"],
        "project-b",
    )
    rule = rejected["importer_rejected"]
    assert len(payload(bad)["rows"]) == rule["rows"]
    for row in payload(bad)["rows"]:
        assert row["status"] == "rejected"
        assert row["code"] == rule["error_code"]
        assert row["field"] == rule["field"]
    assert q(ctx, "SELECT COUNT(*) AS n FROM items WHERE project_id=?", (third,))[0]["n"] == 0
    for spec in rejected["cross_project_expectations"].values():
        assert spec["status"] == "rejected_unknown_field"
        assert spec["error_code"] == "E_INPUT_INVALID"
        assert spec["field"] == "project"


def test_corpus_media_only_null_text(ctx, project):
    allow(ctx, CORPUS)
    path = CORPUS / "import/media_only.jsonl"
    expected = manifest()["files"]["import/media_only.jsonl"]["expected"]
    records = jsonl_records(path)
    assert len(records) == expected["total_rows"] == expected["valid_rows"]
    assert sum(record["text"] is None for record in records) == expected["text_null_count"]
    assert sum(len(record["media"]) for record in records) == expected["media_refs_count"]
    job = run_file(ctx, project, path, "jsonl", ["post", "media_file"], "media-only")
    assert job.state == "partial"
    rows = payload(job)["rows"]
    assert len(rows) == 4
    assert all(row["status"] == "committed" and row["field"] == "media" for row in rows)
    assert all(row["code"] == "E_INPUT_INVALID" for row in rows)
    absent = m.payload_digest({"text": None})
    present = m.payload_digest({"text": ""})
    assert absent != present
    for item in q(ctx, "SELECT id FROM items WHERE project_id=?", (project,)):
        revision = ctx.store.get(m.ItemRevision, item["id"])
        assert revision.text == ""
        assert "text" in revision.missing_fields
        assert revision.text_hash == absent
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 0


def test_corpus_subtitles(ctx):
    allow(ctx, CORPUS)
    files = manifest()["files"]
    for path in sorted((CORPUS / "subtitles").iterdir()):
        spec = files[f"subtitles/{path.name}"]["expected"]
        owner = project_for(ctx, path.stem)
        job = run_file(ctx, owner, path, path.suffix.lstrip("."), ["subtitles"], path.stem)
        segments = q(
            ctx,
            "SELECT * FROM transcript_segments WHERE project_id=? ORDER BY cue_index",
            (owner,),
        )
        assert len(segments) == spec["cue_count"]
        assert all(row["project_id"] == owner and row["asset_id"] is None for row in segments)
        assert all(row["source_id"] and row["method"] == "provided" for row in segments)
        assert all(row["language"] == "und" for row in segments)
        overlaps = [bool(row["overlap"]) for row in segments]
        if spec["has_overlaps"]:
            assert any(overlaps)
        else:
            assert not any(overlaps)
        if spec["error_code"] is None:
            assert job.state == "succeeded"
            assert not payload(job)["inputs"]
            assert all(row.get("code") is None for row in payload(job)["rows"])
        else:
            codes = [row.get("code") for row in payload(job)["rows"]]
            codes.extend(item["code"] for item in payload(job)["inputs"])
            assert spec["error_code"] in codes
            assert job.state == "partial"
            assert spec["valid"] is False
    zh_rows = q(ctx, "SELECT text, language FROM transcript_segments WHERE text LIKE '%<b>%'")
    assert len(zh_rows) == 1
    assert "<b>MetaMuse CLI</b>" in zh_rows[0]["text"]
    multiline = q(ctx, "SELECT text FROM transcript_segments WHERE text LIKE '%請注意多行字幕%'")
    assert "\n" in multiline[0]["text"] and "<i>「本地優先」</i>" in multiline[0]["text"]
    stored = q(ctx, "SELECT text FROM transcript_segments")
    assert all("\ufeff" not in row["text"] for row in stored)


def test_corpus_media_readiness(ctx, project):
    allow(ctx, CORPUS)
    files = manifest()["files"]
    names = ["tiny.mp4", "tiny.wav", "tiny.png", "same_bytes_a.mp4", "same_bytes_b.mp4"]
    inputs = [
        m.LocalFileInput(
            kind="local_file", path=str((CORPUS / "media" / name).resolve()), format="media"
        )
        for name in names
    ]
    job = run_files(ctx, project, inputs, ["media_file"], "media-good")
    assert job.state == "succeeded" and job.stop_reason == "input_exhausted"
    assets = q(ctx, "SELECT * FROM assets")
    by_sha = {row["sha256"]: row for row in assets}
    assert len(assets) == 3
    for name in names:
        spec = files[f"media/{name}"]["expected"]
        digest = hashlib.sha256((CORPUS / "media" / name).read_bytes()).hexdigest()
        asset = by_sha[digest]
        assert asset["state"] == "ready"
        assert asset["mime"] == spec["mime"]
        if spec["duration_seconds"] is None:
            assert asset["duration_ms"] is None
        else:
            assert asset["duration_ms"] == int(float(spec["duration_seconds"]) * 1000)
        if spec.get("shares_bytes_with"):
            other = hashlib.sha256((CORPUS / spec["shares_bytes_with"]).read_bytes()).hexdigest()
            assert digest == other
    mp4 = hashlib.sha256((CORPUS / "media/tiny.mp4").read_bytes()).hexdigest()
    links = q(ctx, "SELECT * FROM source_assets WHERE asset_id=?", (by_sha[mp4]["id"],))
    assert len(links) == 3
    assert len({row["source_id"] for row in links}) == 3
    assert q(ctx, "SELECT COUNT(*) AS n FROM source_assets")[0]["n"] == 5

    for name in ("truncated.mp4", "zero_bytes.mp4", "html_as.mp4"):
        spec = files[f"media/{name}"]["expected"]
        assert spec["valid"] is False
        bad = run_file(ctx, project, CORPUS / "media" / name, "media", ["media_file"], name)
        assert bad.state == "partial"
        assert payload(bad)["inputs"][0]["code"] == spec["error_code"]
        digest = hashlib.sha256((CORPUS / "media" / name).read_bytes()).hexdigest()
        assert digest not in {row["sha256"] for row in q(ctx, "SELECT sha256 FROM assets")}
    assert all(row["state"] == "ready" for row in q(ctx, "SELECT state FROM assets"))


def test_corpus_path_verdicts(tmp_path, monkeypatch):
    rows = [
        json.loads(line)
        for line in (CORPUS / "security/paths.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    expected = manifest()["files"]["security/paths.jsonl"]["expected"]
    assert sum(row["expected_verdict"] == "allow" for row in rows) == expected["allowed_count"]
    assert sum(row["expected_verdict"] == "reject" for row in rows) == expected["rejected_count"]
    runtime = [row for row in rows if row["expected_verdict"] == "runtime_symlink_test"]
    assert len(runtime) == expected["runtime_symlink_test_count"]

    root = tmp_path / "import-root"
    (root / "staging" / "job-101").mkdir(parents=True)
    (root / "staging" / "job-101" / "video.mp4").write_bytes(b"video")
    (root / "attachments").mkdir()
    (root / "attachments" / "figure_01.png").write_bytes(b"png")
    outside = tmp_path / "outside-secret"
    outside.write_bytes(b"secret")
    monkeypatch.chdir(root)
    for row in rows:
        path = row["path"]
        if row["reason"] == "symlink_target_outside_root":
            (root / path).symlink_to(outside)
        elif row["reason"] == "symlink_cycle_detection":
            (root / path).symlink_to(root / path)
        if row["expected_verdict"] == "allow":
            with safety.open_input_file(path, max_bytes=1000, roots=[root]) as handle:
                assert handle.read() in {b"video", b"png"}
            continue
        with pytest.raises(m.CoreError):
            with safety.open_input_file(path, max_bytes=1_000_000, roots=[root]) as handle:
                handle.read()
    assert outside.read_bytes() == b"secret"
