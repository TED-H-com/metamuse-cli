"""Regressions for the B05/B06 adversarial review (R01–R19)."""

import io
import json
import logging
import subprocess
import sys
import time

import pytest
from test_collect import (
    LIMITS,
    allow,
    comment,
    data,
    local,
    q,
    request,
    run,
    submit,
    write_jsonl,
)
from test_media import no_probe, wav_bytes

from metamuse import collect, jobs, media, safety
from metamuse import models as m
from metamuse.store import TransactionWriter

THREE_CUES = (
    "WEBVTT\n\n"
    "00:00:00.000 --> 00:00:01.000\none\n\n"
    "00:00:01.000 --> 00:00:02.000\ntwo\n\n"
    "00:00:02.000 --> 00:00:03.000\nthree\n"
)


def _limits(**extra):
    body = dict(LIMITS)
    body.update(extra)
    return body


def _crash_once(monkeypatch):
    calls = {"n": 0}

    def crash(step):
        del step
        calls["n"] += 1
        if calls["n"] == 1:
            raise SystemExit(7)

    monkeypatch.setattr(collect, "_note_batch", crash)
    return calls


def _probe(duration: str):
    payload = json.dumps(
        {
            "format": {"duration": duration},
            "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}],
        }
    ).encode()

    def fake(argv, **kwargs):
        del argv, kwargs

        class Completed:
            returncode = 0
            stdout = payload

        return Completed()

    return fake


def test_r01_scope_is_part_of_the_persisted_identity(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = write_jsonl(
        root,
        "scoped.jsonl",
        [
            comment("p", "parent", kind="post", native_scope="scope-A"),
            comment("c", "child", parent_native_id="p"),
            {"platform": "generic", "kind": "comment", "text": "weak", "native_scope": "kept"},
            {"platform": "generic", "kind": "comment", "text": "noscope"},
        ],
    )
    job = run(ctx, request(project, [local(path, "jsonl")], ["post", "comments"], key="scoped"))
    assert job.state == "succeeded" and job.error is None
    scopes = {
        row["native_id"]: row["native_scope"]
        for row in q(ctx, "SELECT native_id, native_scope FROM sources WHERE native_id IS NOT NULL")
    }
    assert scopes["p"] == "scope-A"
    assert scopes["c"] == ""
    child = q(ctx, "SELECT parent_id, unresolved_parent FROM items WHERE native_id='c'")[0]
    assert child["parent_id"] is None and child["unresolved_parent"] == "p"
    assert q(ctx, "SELECT native_scope FROM sources WHERE native_scope='kept'")
    assert (
        q(
            ctx,
            "SELECT COUNT(*) AS n FROM sources WHERE native_id IS NULL AND native_scope IS NULL",
        )[0]["n"]
        == 1
    )
    again = write_jsonl(
        root, "reimport.jsonl", [comment("p", "parent-2", kind="post", native_scope="scope-A")]
    )
    reimport = run(ctx, request(project, [local(again, "jsonl")], ["post"], key="reimport"))
    assert reimport.state == "succeeded" and reimport.error is None
    both = write_jsonl(
        root,
        "both.jsonl",
        [comment("x", "alpha", native_scope="A"), comment("x", "beta", native_scope="B")],
    )
    paired = run(ctx, request(project, [local(both, "jsonl")], ["comments"], key="both"))
    assert paired.state == "succeeded"
    assert [row["status"] for row in data(paired)["rows"]] == ["committed", "committed"]
    assert q(ctx, "SELECT COUNT(*) AS n FROM items WHERE native_id='x'")[0]["n"] == 2


def test_r02_every_text_change_keeps_its_revision_and_media(ctx, project, tmp_path, monkeypatch):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    (root / "tone.wav").write_bytes(wav_bytes())
    path = write_jsonl(
        root,
        "revs.jsonl",
        [
            comment("x", "first", media=[{"ref": "tone.wav", "kind": "audio"}]),
            comment("x", "second"),
            comment("x", "third"),
        ],
    )
    job = run(
        ctx,
        request(project, [local(path, "jsonl")], ["comments", "media_file"], key="revs"),
    )
    assert job.state == "succeeded" and job.error is None
    stored = q(ctx, "SELECT revision, text FROM item_revisions ORDER BY revision")
    assert [(row["revision"], row["text"]) for row in stored] == [
        (1, "first"),
        (2, "second"),
        (3, "third"),
    ]
    links = q(ctx, "SELECT revision FROM item_assets")
    assert [row["revision"] for row in links] == [1]
    asset = q(ctx, "SELECT * FROM assets")
    assert len(asset) == 1
    assert (ctx.paths.root / asset[0]["object_path"]).is_file()


def test_r03_partial_subtitle_imports_do_not_look_complete(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = root / "three.vtt"
    path.write_text(THREE_CUES, encoding="utf-8")
    first = run(
        ctx,
        request(
            project,
            [local(path, "vtt")],
            ["subtitles"],
            key="partial-cues",
            language="en",
            limits=_limits(max_items=1),
        ),
    )
    assert first.stop_reason == "limit_reached"
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 1
    second = run(
        ctx,
        request(
            project,
            [local(path, "vtt")],
            ["subtitles"],
            key="rest-cues",
            language="en",
            limits=_limits(max_items=500),
        ),
    )
    assert any(row["status"] == "committed" for row in data(second)["rows"])
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 3
    basis = json.loads(q(ctx, "SELECT use_basis FROM sources")[0]["use_basis"])
    assert basis["complete"] == 1 and basis["language"] == "en"
    third = run(
        ctx,
        request(
            project,
            [local(path, "vtt")],
            ["subtitles"],
            key="zh-cues",
            language="zh-Hant",
        ),
    )
    assert third.state == "succeeded"
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments WHERE language='en'")[0]["n"] == 3
    assert (
        q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments WHERE language='zh-Hant'")[0]["n"]
        == 3
    )
    assert q(ctx, "SELECT COUNT(DISTINCT id) AS n FROM sources")[0]["n"] == 2


def test_r04_sweep_deletes_only_marked_import_directories(ctx, project, tmp_path, caplog):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = write_jsonl(root, "one.jsonl", [comment("n", "甲")])
    job = run(ctx, request(project, [local(path, "jsonl")], ["comments"], key="marker"))
    staging = ctx.paths.root / "staging"
    notes = staging / "job_notes"
    notes.mkdir(parents=True)
    (notes / "keep.txt").write_text("synthetic", encoding="utf-8")
    alien = staging / "job_alien"
    alien.mkdir()
    (alien / ".collect-owner").write_text(
        json.dumps({"job_id": "someone-else", "operation": "import.local"}),
        encoding="utf-8",
    )
    (alien / "keep.txt").write_text("leave", encoding="utf-8")
    owned = staging / job.id
    owned.mkdir()
    (owned / ".collect-owner").write_text(
        json.dumps({"job_id": job.id, "operation": "import.local"}),
        encoding="utf-8",
    )
    (owned / "data.txt").write_text("owned", encoding="utf-8")
    claimed = collect._claim_staging(ctx.store, "job_not_in_db")
    assert (claimed / ".collect-owner").stat().st_mode & 0o777 == 0o600
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE jobs SET operation=? WHERE id=?", ("other", job.id))
    caplog.set_level(logging.DEBUG, logger="metamuse.collect")
    collect._sweep(ctx.store, "job_current", remove_current=True)
    assert (notes / "keep.txt").read_text(encoding="utf-8") == "synthetic"
    assert (alien / "keep.txt").read_text(encoding="utf-8") == "leave"
    assert (owned / "data.txt").read_text(encoding="utf-8") == "owned"
    assert claimed.is_dir()
    assert "job_notes" in caplog.text
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE jobs SET operation=? WHERE id=?", ("import.local", job.id))
    collect._sweep(ctx.store, "job_current", remove_current=True)
    assert not owned.exists()
    assert (notes / "keep.txt").is_file()
    assert (alien / "keep.txt").is_file()


def test_r05_ready_requires_a_usable_ffprobe_stream(ctx, project, tmp_path, monkeypatch):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    ftyp = (16).to_bytes(4, "big") + b"ftyp" + b"isom" + b"\x00\x00\x00\x00"
    cases = {
        "sig.png": b"\x89PNG\r\n\x1a\n",
        "sig.gif": b"GIF89a",
        "sig.jpg": b"\xff\xd8\xff",
        "sig.mp4": ftyp,
    }
    assert len(cases["sig.png"]) == 8
    assert len(cases["sig.mp4"]) == 16
    box = root / "sig.mp4"
    box.write_bytes(ftyp)
    media.walk_isobmff(box)
    for name, payload in cases.items():
        path = root / name
        path.write_bytes(payload)
        job = run(ctx, request(project, [local(path, "media")], ["media_file"], key=name))
        assert job.state == "succeeded" and job.error is None
        asset = q(ctx, "SELECT state FROM assets ORDER BY rowid DESC LIMIT 1")[0]
        assert asset["state"] == "unavailable"
        basis = json.loads(
            q(ctx, "SELECT use_basis FROM sources ORDER BY rowid DESC LIMIT 1")[0]["use_basis"]
        )
        assert basis["probe"] == "not_installed"
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets WHERE state='ready'")[0]["n"] == 0

    tone = tmp_path / "tone.wav"
    tone.write_bytes(wav_bytes())
    monkeypatch.setattr(media.shutil, "which", lambda _name: "/usr/bin/ffprobe")

    def empty(argv, **kwargs):
        del argv, kwargs

        class Completed:
            returncode = 0
            stdout = b'{"format":{"duration":"1"}}'

        return Completed()

    monkeypatch.setattr(media, "_run_ffprobe", empty)
    with pytest.raises(m.CoreError) as rejected:
        media.analyze_file(tone, max_bytes=100_000, read_timeout_seconds=5)
    assert rejected.value.detail.message == "Media probe found no usable stream"

    no_probe(monkeypatch)
    sample = root / "tone.wav"
    sample.write_bytes(wav_bytes())
    plain = run(ctx, request(project, [local(sample, "media")], ["media_file"], key="plain"))
    assert plain.state == "succeeded"
    assert q(ctx, "SELECT state, duration_ms FROM assets WHERE mime='audio/wav'")[0]["state"] == (
        "unavailable"
    )
    monkeypatch.setattr(media.shutil, "which", lambda _name: "/usr/bin/ffprobe")
    monkeypatch.setattr(media, "_run_ffprobe", _probe("1.5"))
    upgraded = run(ctx, request(project, [local(sample, "media")], ["media_file"], key="up"))
    assert upgraded.state == "succeeded"
    row = q(ctx, "SELECT state, duration_ms FROM assets WHERE mime='audio/wav'")[0]
    assert row["state"] == "ready" and row["duration_ms"] == 1500
    monkeypatch.setattr(media, "_run_ffprobe", _probe("9"))
    again = run(ctx, request(project, [local(sample, "media")], ["media_file"], key="later"))
    assert again.state == "succeeded"
    row = q(ctx, "SELECT state, duration_ms FROM assets WHERE mime='audio/wav'")[0]
    assert row["state"] == "ready" and row["duration_ms"] == 1500
    no_probe(monkeypatch)
    kept = run(ctx, request(project, [local(sample, "media")], ["media_file"], key="keep"))
    assert kept.state == "succeeded"
    row = q(ctx, "SELECT state, duration_ms FROM assets WHERE mime='audio/wav'")[0]
    assert row["state"] == "ready" and row["duration_ms"] == 1500


def test_r07_parser_bounds_memory_nesting_and_overlap_work(ctx, project, tmp_path):
    del ctx, project, tmp_path
    script = (
        "import resource\n"
        "limit = 512 * 1024 * 1024\n"
        "resource.setrlimit(resource.RLIMIT_AS, (limit, limit))\n"
        "from metamuse.collect import parse_json\n"
        "rows = parse_json(b'[' + b'0,' * 1000000 + b'0]')\n"
        "assert len(rows) < 1000, len(rows)\n"
        "assert rows[-1]['code'] == 'E_LIMIT_REACHED'\n"
        "assert 'remainder not inspected' in rows[-1]['message']\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr

    raw = b"\n".join(b"{" for _ in range(80)) + b"\n"
    bounded = collect.parse_jsonl(raw, max_records=500, max_errors=50)
    assert len(bounded) == 51
    assert bounded[-1]["message"] == "30 additional record errors omitted"

    good = json.dumps(comment("ok", "kept")).encode()
    deep = (
        b'{"platform":"generic","kind":"comment","native_id":"deep","text":'
        + b"[" * 20000
        + b"0"
        + b"]" * 20000
        + b"}"
    )
    nested = collect.parse_jsonl(good + b"\n" + deep + b"\n" + good + b"\n")
    assert isinstance(nested[0], collect.Record) and nested[0].text == "kept"
    assert nested[1]["code"] == "E_LIMIT_REACHED" and "32" in nested[1]["message"]
    assert isinstance(nested[2], collect.Record)

    def stop():
        raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "stopped")

    many = b"\n".join(json.dumps(comment(f"n{i}", "x")).encode() for i in range(70)) + b"\n"
    with pytest.raises(m.CoreError) as cancelled:
        collect.parse_jsonl(many, progress=stop, max_records=500)
    assert cancelled.value.detail.code == m.ErrorCode.LIMIT_REACHED

    def stamp(ms: int) -> str:
        seconds, milli = divmod(ms, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milli:03d}"

    cues = ["WEBVTT\n\n"]
    for index in range(12000):
        start = index * 10
        cues.append(f"{stamp(start)} --> {stamp(start + 5)}\n{index}\n\n")
    started = time.perf_counter()
    parsed = media.parse_webvtt("".join(cues))
    elapsed = time.perf_counter() - started
    assert len(parsed.cues) == 12000
    assert not any(cue.overlap for cue in parsed.cues)
    assert elapsed < 1.0, elapsed


def test_r08_row_faults_stay_on_the_row(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    body = json.dumps(comment("ok", "kept"), ensure_ascii=False).encode() + b"\n"
    body += b'{"platform":"generic","kind":"comment","native_id":"bad","text":"\\ud800"}\n'
    deep = (
        b'{"platform":"generic","kind":"comment","native_id":"deep","text":'
        + b"[" * 20000
        + b"0"
        + b"]" * 20000
        + b"}\n"
    )
    path = root / "rows.jsonl"
    path.write_bytes(body + deep)
    job = run(ctx, request(project, [local(path, "jsonl")], ["comments"], key="rows"))
    assert job.state == "partial" and job.error is None and job.stop_reason == "row_errors"
    rows = data(job)["rows"]
    assert rows[0]["status"] == "committed"
    assert rows[1]["code"] == "E_INPUT_INVALID" and "Unicode" in rows[1]["message"]
    assert rows[2]["code"] == "E_LIMIT_REACHED"
    assert q(ctx, "SELECT COUNT(*) AS n FROM items")[0]["n"] == 1

    wide = root / "wide.csv"
    long = "a" * 140_000
    wide.write_text(
        f"platform,kind,native_id,text\ngeneric,comment,a,first\ngeneric,comment,b,{long}\n",
        encoding="utf-8",
    )
    csv_job = run(ctx, request(project, [local(wide, "csv")], ["comments"], key="wide"))
    assert csv_job.state == "succeeded"
    stored = q(
        ctx,
        "SELECT text FROM item_revisions WHERE item_id IN "
        "(SELECT id FROM items WHERE native_id='b')",
    )
    assert len(stored[0]["text"]) == 140_000
    broken = collect.parse_csv(
        b'platform,kind,text\ngeneric,comment,"first\ngeneric,comment,second\n'
    )
    assert not any(
        isinstance(row, collect.Record) and row.text and "second" in row.text for row in broken
    )
    assert any(isinstance(row, dict) and row["code"] == "E_INPUT_INVALID" for row in broken)

    srt = root / "hours.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nok\n\n"
        "2\n100000000000000000000:00:00,000 --> 100000000000000000000:00:01,000\nbad\n",
        encoding="utf-8",
    )
    captions = run(ctx, request(project, [local(srt, "srt")], ["subtitles"], key="hours"))
    assert captions.state == "partial" and captions.error is None
    assert captions.stop_reason == "row_errors"
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 1
    ordinary = media.parse_srt("1\n100:00:00,000 --> 100:00:01,000\nstill\n")
    assert ordinary.cues[0].start_ms == 100 * 3600 * 1000


def test_r09_cue_text_and_style_identifiers_stay_intact():
    parsed = media.parse_webvtt(
        "WEBVTT\n\nSTYLE-cue\n00:00:01.000 --> 00:00:02.000\n甲\u2028乙\u0085丙\r\n尾\n"
    )
    assert parsed.errors == []
    assert len(parsed.cues) == 1
    assert parsed.cues[0].text == "甲\u2028乙\u0085丙\r\n尾"
    skipped = media.parse_webvtt(
        "WEBVTT\n\nSTYLE\n::cue { color: white; }\n\n00:00:01.000 --> 00:00:02.000\nkept\n"
    )
    assert [cue.text for cue in skipped.cues] == ["kept"]


def test_r10_dotdot_after_a_symlink_is_not_rewritten(tmp_path):
    root = tmp_path / "root"
    inside = root / "in"
    other = root / "other"
    inside.mkdir(parents=True)
    (other / "child").mkdir(parents=True)
    (inside / "file.txt").write_text("inside", encoding="utf-8")
    (other / "file.txt").write_text("outside", encoding="utf-8")
    (inside / "jump").symlink_to(other / "child")
    ambiguous = inside / "jump" / ".." / "file.txt"
    with pytest.raises(m.CoreError) as error:
        with safety.open_input_file(str(ambiguous), max_bytes=100, roots=[root]):
            pass
    assert error.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert (other / "file.txt").read_text(encoding="utf-8") == "outside"
    media_dir = root / "media"
    media_dir.mkdir()
    (media_dir / "tiny.txt").write_bytes(b"bytes")
    nested = root / "import" / ".." / "media" / "tiny.txt"
    with safety.open_input_file(str(nested), max_bytes=100, roots=[root]) as handle:
        assert handle.read() == b"bytes"


def test_r11_restart_keeps_the_byte_budget(ctx, project, tmp_path, monkeypatch):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    raw = wav_bytes()
    (root / "tone.wav").write_bytes(raw)
    rows = [
        comment(f"n{index}", f"row-{index}", media=[{"ref": "tone.wav", "kind": "audio"}])
        for index in range(51)
    ]
    path = write_jsonl(root, "budget.jsonl", rows)
    budget = path.stat().st_size + 50 * len(raw)
    _crash_once(monkeypatch)
    env = submit(
        ctx,
        request(
            project,
            [local(path, "jsonl")],
            ["comments", "media_file"],
            key="budget",
            limits=_limits(max_bytes=budget),
        ),
    )
    with pytest.raises(SystemExit):
        with jobs.Executor(ctx.store) as executor:
            executor.run_once()
    saved = json.loads(
        q(ctx, "SELECT checkpoint FROM job_steps WHERE job_id=?", (env.data["job_id"],))[0][
            "checkpoint"
        ]
    )
    assert saved["bytes_used"] == budget
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_assets")[0]["n"] == 50
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert job.coverage_status != "scope_satisfied"
    assert job.stop_reason != "input_exhausted"
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_assets")[0]["n"] == 50
    assert data(job)["rows"][-1]["code"] == "E_LIMIT_REACHED"


def test_r12_asset_id_is_in_the_checkpoint_before_restart(ctx, project, tmp_path, monkeypatch):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = root / "tone.wav"
    path.write_bytes(wav_bytes())
    _crash_once(monkeypatch)
    env = submit(ctx, request(project, [local(path, "media")], ["media_file"], key="asset-id"))
    with pytest.raises(SystemExit):
        with jobs.Executor(ctx.store) as executor:
            executor.run_once()
    saved = json.loads(
        q(ctx, "SELECT checkpoint FROM job_steps WHERE job_id=?", (env.data["job_id"],))[0][
            "checkpoint"
        ]
    )
    asset_id = q(ctx, "SELECT id FROM assets")[0]["id"]
    assert saved["rows"][0]["asset_id"] == asset_id
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert data(job)["rows"][0]["asset_id"] == asset_id
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 1
    assert q(ctx, "SELECT COUNT(*) AS n FROM source_assets")[0]["n"] == 1


def test_r13_repeating_a_direct_attachment_keeps_the_new_source(
    ctx, project, tmp_path, monkeypatch
):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    created = run(
        ctx,
        request(project, [m.TextInput(kind="text", text="一則")], ["post"], key="host"),
    )
    item_id = data(created)["rows"][0]["item_id"]
    path = root / "tone.wav"
    path.write_bytes(wav_bytes())
    first = run(
        ctx,
        request(
            project,
            [local(path, "media")],
            ["media_file"],
            key="attach-1",
            attach_item_id=item_id,
        ),
    )
    assert first.state == "succeeded"
    second = run(
        ctx,
        request(
            project,
            [local(path, "media")],
            ["media_file"],
            key="attach-2",
            attach_item_id=item_id,
        ),
    )
    assert second.state == "succeeded" and second.error is None
    assert q(ctx, "SELECT COUNT(*) AS n FROM source_assets")[0]["n"] == 2
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_assets")[0]["n"] == 1
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 1


def test_r14_changed_bytes_and_mime_conflicts_are_rejected(ctx, project, tmp_path, monkeypatch):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = root / "tone.wav"
    original = wav_bytes()
    path.write_bytes(original)
    ctx.store.put_object(io.BytesIO(original), mime="image/png")
    conflict = run(ctx, request(project, [local(path, "media")], ["media_file"], key="png"))
    assert conflict.state == "partial"
    assert data(conflict)["inputs"][0]["code"] == "E_INPUT_INVALID"
    assert "MIME" in data(conflict)["inputs"][0]["message"]
    stored = q(ctx, "SELECT mime, state FROM assets")
    assert len(stored) == 1
    assert stored[0]["mime"] == "image/png" and stored[0]["state"] == "ready"

    other = tmp_path / "staged.wav"
    flipped = bytearray(original)
    flipped[-1] ^= 0x01
    other.write_bytes(original)
    info = media.analyze_file(other, max_bytes=100_000, read_timeout_seconds=5)
    other.write_bytes(flipped)
    with pytest.raises(m.CoreError) as changed:
        media.place_object(ctx.store, other, info)
    assert changed.value.detail.message == "Media bytes changed after they were measured"

    placed = tmp_path / "placed.wav"
    placed.write_bytes(bytes(flipped))
    measured = media.analyze_file(placed, max_bytes=100_000, read_timeout_seconds=5)
    media.place_object(ctx.store, placed, measured)
    target = ctx.store.paths.object_path(measured.sha256)
    corrupt = bytearray(target.read_bytes())
    corrupt[-1] ^= 0x01
    target.write_bytes(corrupt)
    with pytest.raises(m.CoreError) as mismatch:
        with ctx.store.transaction() as conn:
            media.ensure_asset(ctx.store, TransactionWriter(ctx.store, conn), measured)
    assert mismatch.value.detail.message == "Media bytes changed after they were measured"
    assert (
        q(ctx, "SELECT COUNT(*) AS n FROM assets WHERE sha256=?", (measured.sha256,))[0]["n"] == 0
    )


def test_r16_imported_null_text_stays_sql_null(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = write_jsonl(
        root,
        "nulls.jsonl",
        [
            {"platform": "generic", "kind": "post", "native_id": "none", "text": None},
            {"platform": "generic", "kind": "post", "native_id": "empty", "text": ""},
        ],
    )
    job = run(ctx, request(project, [local(path, "jsonl")], ["post"], key="nulls"))
    assert job.state == "succeeded"
    rows = {
        row["native_id"]: row
        for row in q(
            ctx,
            "SELECT i.native_id, r.text, r.missing_fields, r.text_hash "
            "FROM item_revisions r JOIN items i ON i.id=r.item_id",
        )
    }
    assert rows["none"]["text"] is None
    assert json.loads(rows["none"]["missing_fields"]) == ["text"]
    assert rows["none"]["text_hash"] == m.payload_digest({"text": None})
    assert rows["empty"]["text"] == ""
    assert json.loads(rows["empty"]["missing_fields"]) == []
    assert rows["empty"]["text_hash"] == m.payload_digest({"text": ""})
    assert rows["none"]["text_hash"] != rows["empty"]["text_hash"]


def test_r17_unknown_fields_do_not_echo_source_text():
    sentinel = "SOURCE_TEXT_SENTINEL\n" + "x" * 10000
    line = json.dumps(
        {"platform": "generic", "kind": "comment", "text": "ok", sentinel: 1},
        ensure_ascii=False,
    )
    rows = collect.parse_jsonl(line.encode() + b"\n")
    assert rows[0]["field"] == "record"
    assert rows[0]["message"] == "Record contains an unknown field"
    assert "SOURCE_TEXT_SENTINEL" not in rows[0]["message"]
    assert "\n" not in rows[0]["message"]
    assert len(rows[0]["message"]) < 80
    with pytest.raises(collect._FileInvalid) as error:
        collect.parse_csv(f"platform,kind,{sentinel}\n".encode())
    assert error.value.message == "CSV header has an unknown column"
    assert "SOURCE_TEXT_SENTINEL" not in error.value.message


def test_r18_unrequested_media_is_not_opened(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = write_jsonl(
        root,
        "ref.jsonl",
        [comment("c", "hello", media=[{"ref": "missing.bin", "kind": "file"}])],
    )
    quiet = run(ctx, request(project, [local(path, "jsonl")], ["comments"], key="comments-only"))
    assert quiet.state == "succeeded" and quiet.stop_reason == "input_exhausted"
    assert data(quiet)["rows"][0]["code"] is None
    evidence = json.loads(q(ctx, "SELECT evidence FROM item_revisions")[0]["evidence"])
    assert evidence["media"] == [{"ref": "missing.bin", "kind": "file"}]
    loud = run(
        ctx,
        request(project, [local(path, "jsonl")], ["comments", "media_file"], key="with-media"),
    )
    assert loud.state == "partial"
    assert data(loud)["rows"][0]["field"] == "media"


def test_r19_limit_lookahead_reports_a_row_that_cannot_be_accepted(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = write_jsonl(
        root,
        "look.jsonl",
        [comment("x", "first"), comment("x", "nope", kind="reply")],
    )
    job = run(
        ctx,
        request(
            project,
            [local(path, "jsonl")],
            ["comments"],
            key="look",
            limits=_limits(max_items=1),
        ),
    )
    assert job.stop_reason == "row_errors"
    assert job.coverage_status == "partial"
    assert job.state == "partial" and job.error is None
    assert [row["status"] for row in data(job)["rows"]] == ["committed", "rejected"]
    assert data(job)["rows"][1]["field"] == "kind"
    assert data(job)["rows"][1]["code"] == "E_REFERENCE_INVALID"
    assert q(ctx, "SELECT COUNT(*) AS n FROM items")[0]["n"] == 1


def test_rr_l2_cue_text_over_one_million_characters_is_a_row_error(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    exact = "a" * 1_000_000
    over = "b" * 1_000_001
    srt = root / "cues.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nlead\n\n"
        f"2\n00:00:01,000 --> 00:00:02,000\n{exact}\n\n"
        f"3\n00:00:02,000 --> 00:00:03,000\n{over}\n\n"
        "4\n00:00:03,000 --> 00:00:04,000\ntail\n",
        encoding="utf-8",
    )
    job = run(ctx, request(project, [local(srt, "srt")], ["subtitles"], key="cue-cap"))
    assert job.state == "partial" and job.error is None
    assert job.stop_reason == "row_errors"
    rows = data(job)["rows"]
    assert [row["status"] for row in rows] == ["committed", "committed", "rejected", "committed"]
    assert rows[2]["field"] == "text"
    assert rows[2]["code"] == "E_LIMIT_REACHED"
    assert rows[2]["message"] == "text exceeds 1000000 characters"
    assert over not in rows[2]["message"]
    stored = [
        row["text"] for row in q(ctx, "SELECT text FROM transcript_segments ORDER BY cue_index")
    ]
    assert stored == ["lead", exact, "tail"]
    basis = json.loads(q(ctx, "SELECT use_basis FROM sources")[0]["use_basis"])
    assert basis["complete"] == 0
