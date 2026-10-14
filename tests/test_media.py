"""Local media bytes and provided subtitles (B06; AT-005, AT-006, AT-007)."""

import hashlib
import io
import json
import os
import shlex
import shutil
import struct
import time

import pytest
from test_collect import FIXTURES, allow, data, local, q, request, run

from metamuse import app, jobs, media
from metamuse import models as m

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PNG_OTHER = b"\x89PNG\r\n\x1a\n" + b"\x11" * 16


def wav_bytes() -> bytes:
    frames = b"\x00\x00" * 8
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    chunk = (
        b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(frames)) + frames
    )
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WAVE" + chunk


def mp4_bytes() -> bytes:
    body = b"isom" + b"\x00\x00\x00\x00"
    return (8 + len(body)).to_bytes(4, "big") + b"ftyp" + body


def no_probe(monkeypatch) -> None:
    monkeypatch.setattr(media.shutil, "which", lambda _name: None)


def test_sniff_uses_magic_bytes_and_walk_detects_truncation(tmp_path):
    assert media.sniff_mime(PNG) == "image/png"
    assert media.sniff_mime(b"\xff\xd8\xff" + b"\x00" * 8) == "image/jpeg"
    assert media.sniff_mime(b"GIF89a" + b"\x00" * 8) == "image/gif"
    assert media.sniff_mime(wav_bytes()) == "audio/wav"
    assert media.sniff_mime(mp4_bytes()) == "video/mp4"
    quicktime = bytearray(mp4_bytes())
    quicktime[8:12] = b"qt  "
    assert media.sniff_mime(bytes(quicktime)) == "video/quicktime"
    assert media.sniff_mime(b"<html><body>nope</body></html>") == "text/html"
    assert media.sniff_mime(b"\xef\xbb\xbf\n<html>") == "text/html"
    assert media.sniff_mime(b"not a media file") is None
    named = tmp_path / "title.mp4"
    named.write_bytes(PNG)
    assert media.sniff_mime(named.read_bytes()[:64]) == "image/png"
    good = tmp_path / "good.mp4"
    good.write_bytes(mp4_bytes())
    media.walk_isobmff(good)
    truncated = tmp_path / "short.mp4"
    truncated.write_bytes(b"\x00\x00\x00\x20ftypisom")
    with pytest.raises(m.CoreError) as error:
        media.walk_isobmff(truncated)
    assert error.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert "truncated" in error.value.detail.message


def test_structural_rejection_does_not_need_ffprobe(tmp_path, monkeypatch):
    no_probe(monkeypatch)
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    html = tmp_path / "page.mp4"
    html.write_bytes(b"<html><body>saved page</body></html>")
    truncated = tmp_path / "cut.mp4"
    truncated.write_bytes(b"\x00\x00\x00\x20ftypisom")
    unknown = tmp_path / "blob.bin"
    unknown.write_bytes(b"zzzz-not-media")
    png = tmp_path / "clip.mp4"
    png.write_bytes(PNG)
    for path, message in (
        (empty, "empty"),
        (html, "HTML"),
        (truncated, "truncated"),
        (unknown, "not recognized"),
    ):
        with pytest.raises(m.CoreError) as error:
            media.analyze_file(path, max_bytes=1000, read_timeout_seconds=5)
        assert message in error.value.detail.message
    info = media.analyze_file(png, max_bytes=1000, read_timeout_seconds=5)
    assert info.mime == "image/png" and info.probe == "not_installed"
    assert info.byte_size == len(PNG)


def test_ffprobe_uses_a_fixed_argv_without_a_shell(tmp_path, monkeypatch):
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs

        class Completed:
            returncode = 0
            stdout = (
                b'{"format":{"duration":"1.5"},'
                b'"streams":[{"codec_type":"audio","codec_name":"pcm_s16le"}]}'
            )

        return Completed()

    monkeypatch.setattr(media.shutil, "which", lambda _name: "/usr/bin/ffprobe")
    monkeypatch.setattr(media, "_run_ffprobe", fake_run)
    info = media.analyze_file(path, max_bytes=100_000, read_timeout_seconds=7)
    assert info.probe == "probed" and info.duration_ms == 1500 and info.mime == "audio/wav"
    assert seen["kwargs"]["timeout"] == 7
    assert "shell" not in seen["kwargs"]
    assert seen["argv"][:-1] == ["/usr/bin/ffprobe", *media.FFPROBE_ARGS]
    assert seen["argv"][-1] == f"file:{path.resolve()}"
    assert seen["argv"][-1].startswith("file:/")


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is not installed")
def test_installed_ffprobe_accepts_a_wav(tmp_path):
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    info = media.analyze_file(path, max_bytes=100_000, read_timeout_seconds=15)
    assert info.probe == "probed" and info.mime == "audio/wav" and info.duration_ms is not None


def test_probe_failures_and_a_missing_object_are_not_ready(ctx, tmp_path, monkeypatch):
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    monkeypatch.setattr(media.shutil, "which", lambda _name: "/usr/bin/ffprobe")

    def reject(argv, **kwargs):
        del argv, kwargs

        class Completed:
            returncode = 1
            stdout = b""

        return Completed()

    monkeypatch.setattr(media, "_run_ffprobe", reject)
    with pytest.raises(m.CoreError) as rejected:
        media.analyze_file(path, max_bytes=100_000, read_timeout_seconds=5)
    assert rejected.value.detail.message == "Media probe rejected the file"

    def timeout(argv, **kwargs):
        del argv, kwargs
        raise media.subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=5)

    monkeypatch.setattr(media, "_run_ffprobe", timeout)
    with pytest.raises(m.CoreError) as timed:
        media.analyze_file(path, max_bytes=100_000, read_timeout_seconds=5)
    assert timed.value.detail.code == m.ErrorCode.NETWORK_TIMEOUT

    def vanish(src, dst):
        del dst
        os.unlink(src)

    monkeypatch.setattr(media.shutil, "which", lambda _name: None)
    monkeypatch.setattr(media.os, "replace", vanish)
    info = media.analyze_file(path, max_bytes=100_000, read_timeout_seconds=5)
    with pytest.raises(m.CoreError) as missing:
        media.place_object(ctx.store, path, info)
    assert missing.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert "missing" in missing.value.detail.message
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 0


def test_identical_bytes_share_one_asset_and_keep_both_sources(ctx, project, tmp_path, monkeypatch):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    first = root / "my-title-video.wav"
    second = root / "other-title.wav"
    first.write_bytes(wav_bytes())
    second.write_bytes(wav_bytes())
    job_a = run(ctx, request(project, [local(first, "media")], ["media_file"], key="media-a"))
    job_b = run(ctx, request(project, [local(second, "media")], ["media_file"], key="media-b"))
    assert data(job_a)["rows"][0]["status"] == "committed"
    assert data(job_a)["rows"][0]["asset_id"]
    assert data(job_a)["rows"][0]["asset_id"] == data(job_b)["rows"][0]["asset_id"]
    assets = q(ctx, "SELECT * FROM assets")
    assert len(assets) == 1 and assets[0]["state"] == "unavailable"
    assert assets[0]["mime"] == "audio/wav"
    assert "title" not in assets[0]["object_path"]
    assert (
        assets[0]["object_path"]
        == f"objects/sha256/{assets[0]['sha256'][:2]}/{assets[0]['sha256']}"
    )
    located = ctx.paths.root / assets[0]["object_path"]
    assert located.read_bytes() == wav_bytes()
    assert located.stat().st_mode & 0o777 == 0o600
    assert q(ctx, "SELECT COUNT(*) AS n FROM sources WHERE project_id=?", (project,))[0]["n"] == 2
    assert q(ctx, "SELECT COUNT(*) AS n FROM source_assets")[0]["n"] == 2
    other = app.create_project(ctx, {"name": "Other", "request_key": "other-media"}).data["id"]
    with pytest.raises(m.CoreError):
        with ctx.store.connection() as conn:
            ctx.store.require_project_asset(other, assets[0]["id"], conn=conn)
    run(ctx, request(other, [local(first, "media")], ["media_file"], key="media-other"))
    with ctx.store.connection() as conn:
        ctx.store.require_project_asset(other, assets[0]["id"], conn=conn)
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 1
    assert (
        q(ctx, "SELECT COUNT(*) AS n FROM source_assets WHERE project_id=?", (other,))[0]["n"] == 1
    )


def test_conflicting_mime_keeps_the_first_classification(ctx, project, tmp_path, monkeypatch):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = root / "clip.wav"
    raw = wav_bytes()
    path.write_bytes(raw)
    ctx.store.put_object(io.BytesIO(raw), mime="application/octet-stream")
    job = run(ctx, request(project, [local(path, "media")], ["media_file"], key="mime-conflict"))
    assert job.state == "partial"
    assert data(job)["inputs"][0]["code"] == "E_INPUT_INVALID"
    assert "MIME" in data(job)["inputs"][0]["message"]
    assets = q(ctx, "SELECT * FROM assets")
    assert len(assets) == 1
    assert assets[0]["mime"] == "application/octet-stream" and assets[0]["state"] == "ready"
    assert q(ctx, "SELECT COUNT(*) AS n FROM source_assets")[0]["n"] == 0


def test_bad_media_files_never_become_ready(ctx, project, tmp_path, monkeypatch):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    cases = {
        "empty.mp4": b"",
        "page.mp4": b"<html><body>not a movie</body></html>",
        "cut.mp4": b"\x00\x00\x00\x20ftypisom",
    }
    for name, payload in cases.items():
        path = root / name
        path.write_bytes(payload)
        job = run(
            ctx,
            request(project, [local(path, "media")], ["media_metadata", "media_file"], key=name),
        )
        assert job.state == "partial" and job.error is None
        assert data(job)["inputs"][0]["code"] in {"E_INPUT_INVALID", "E_UNSUPPORTED"}
        assert data(job)["rows"] == []
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 0

    stolen = root / "stolen.wav"
    stolen.write_bytes(wav_bytes())

    def vanish(src, dst):
        del dst
        os.unlink(src)

    monkeypatch.setattr(media.os, "replace", vanish)
    job = run(ctx, request(project, [local(stolen, "media")], ["media_file"], key="stolen"))
    assert data(job)["inputs"][0]["code"] == "E_INPUT_INVALID"
    assert "missing" in data(job)["inputs"][0]["message"]
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 0


def test_referenced_media_dedupes_bytes_and_keeps_item_errors(ctx, project, tmp_path, monkeypatch):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    picture = root / "pic.png"
    picture.write_bytes(PNG)
    rows = [
        {
            "platform": "generic",
            "kind": "comment",
            "native_id": "left",
            "text": "左",
            "media": [{"ref": "pic.png", "kind": "image"}],
        },
        {
            "platform": "generic",
            "kind": "comment",
            "native_id": "right",
            "text": "右",
            "media": [{"ref": "pic.png", "kind": "image"}],
        },
    ]
    path = root / "with-media.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    job = run(
        ctx,
        request(project, [local(path, "jsonl")], ["comments", "media_file"], key="refs"),
    )
    assert [row["status"] for row in data(job)["rows"]] == ["committed", "committed"]
    assert data(job)["rows"][0]["asset_id"] == data(job)["rows"][1]["asset_id"]
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 1
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_assets")[0]["n"] == 2
    meta = root / "meta.jsonl"
    meta.write_text(
        json.dumps(
            {
                "platform": "generic",
                "kind": "comment",
                "native_id": "meta-only",
                "text": "只記類型",
                "media": [{"ref": "pic.png", "kind": "image"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    meta_job = run(
        ctx,
        request(project, [local(meta, "jsonl")], ["comments", "media_metadata"], key="meta"),
    )
    assert meta_job.state == "succeeded"
    meta_id = data(meta_job)["rows"][0]["item_id"]
    evidence = json.loads(
        q(ctx, "SELECT evidence FROM item_revisions WHERE item_id=?", (meta_id,))[0]["evidence"]
    )
    assert evidence["media"][0]["mime"] == "image/png"
    assert data(meta_job)["rows"][0]["asset_id"] is None
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 1
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_assets")[0]["n"] == 2

    missing = root / "missing-media.jsonl"
    missing.write_text(
        json.dumps(
            {
                "platform": "generic",
                "kind": "comment",
                "native_id": "gap",
                "text": "缺圖",
                "media": [{"ref": "nope.png", "kind": "image"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    failed = run(
        ctx,
        request(project, [local(missing, "jsonl")], ["comments", "media_file"], key="gap"),
    )
    assert failed.state == "partial" and failed.stop_reason == "row_errors"
    assert data(failed)["rows"][0]["status"] == "committed"
    assert data(failed)["rows"][0]["field"] == "media"
    revision = q(
        ctx,
        "SELECT missing_fields FROM item_revisions WHERE item_id=?",
        (data(failed)["rows"][0]["item_id"],),
    )[0]
    assert "media" in json.loads(revision["missing_fields"])
    assert q(ctx, "SELECT COUNT(*) AS n FROM items WHERE native_id='gap'")[0]["n"] == 1


def test_referenced_media_is_read_when_the_job_runs(ctx, project, tmp_path, monkeypatch):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    picture = root / "pic.png"
    picture.write_bytes(PNG)
    path = root / "late.jsonl"
    path.write_text(
        json.dumps(
            {
                "platform": "generic",
                "kind": "post",
                "native_id": "late",
                "text": "圖",
                "media": [{"ref": "pic.png", "kind": "image"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    env = app.submit_collect(
        ctx, request(project, [local(path, "jsonl")], ["post", "media_file"], key="late")
    )
    picture.write_bytes(PNG_OTHER)
    assert env.status == "accepted"
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert job.state == "succeeded"
    asset = q(ctx, "SELECT sha256 FROM assets")[0]
    assert asset["sha256"] != hashlib.sha256(PNG).hexdigest()
    assert asset["sha256"] == hashlib.sha256(PNG_OTHER).hexdigest()


def test_subtitle_parser_keeps_cues_and_rejects_bad_timestamps():
    text = (
        "\ufeffWEBVTT\r\n"
        "Language: zh-Hant\r\n"
        "\r\n"
        "NOTE synthetic\r\n"
        "\r\n"
        "STYLE\r\n"
        "::cue { color: white; }\r\n"
        "\r\n"
        "00:00:01.000 --> 00:00:04.000 align:start line:0%\r\n"
        "<c.red>下次活動是否有字幕？</c>\r\n"
        "這是自製測試資料。👨‍👩‍👧‍👦🇹🇼\r\n"
        "\r\n"
        "00:00:04.000 --> 00:00:05.000\r\n"
        "接著\r\n"
        "\r\n"
        "00:00:03.500 --> 00:00:04.500\r\n"
        "重疊\r\n"
        "\r\n"
        "00:00:09.000 --> 00:00:08.000\r\n"
        "倒序\r\n"
        "\r\n"
        "not a cue\r\n"
    )
    parsed = media.parse_webvtt(text)
    assert parsed.language == "zh-Hant"
    assert [cue.text for cue in parsed.cues] == [
        "<c.red>下次活動是否有字幕？</c>\r\n這是自製測試資料。👨‍👩‍👧‍👦🇹🇼",
        "接著",
        "重疊",
    ]
    assert [cue.overlap for cue in parsed.cues] == [True, True, True]
    assert parsed.cues[0].start_ms == 1000 and parsed.cues[0].end_ms == 4000
    assert parsed.cues[1].start_ms == 4000
    assert {error.field for error in parsed.errors} == {"end_ms", "timestamp"}
    touching = media.parse_webvtt(
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n甲\n\n00:00:02.000 --> 00:00:03.000\n乙\n"
    )
    assert [cue.overlap for cue in touching.cues] == [False, False]
    point = media.parse_webvtt("WEBVTT\n\n00:01.5 --> 00:01.5\n點\n")
    assert point.cues[0].start_ms == point.cues[0].end_ms == 1500
    with pytest.raises(m.CoreError) as html:
        media.parse_webvtt("<html><body>not captions</body></html>")
    assert html.value.detail.code == m.ErrorCode.UNSUPPORTED
    header = media.parse_webvtt("WEBVTT\n")
    assert header.cues == [] and header.errors == []
    srt = media.parse_srt(
        "\ufeff1\r\n00:00:01,000 --> 00:00:02,500\r\n第一行\r\n第二行\r\n\r\n"
        "2\r\n00:00:02,500 --> 00:00:03,000\r\n接著\r\n"
    )
    assert srt.cues[0].text == "第一行\r\n第二行"
    assert [cue.overlap for cue in srt.cues] == [False, False]
    broken = media.parse_srt("1\n\n")
    assert broken.errors[0].field == "timestamp" and broken.cues == []
    assert media.parse_srt("   \n").cues == []
    with pytest.raises(m.CoreError):
        media.parse_srt("<html><body>nope</body></html>")


def test_subtitle_import_attaches_dedupes_and_flags_overlaps(ctx, project, tmp_path, monkeypatch):
    no_probe(monkeypatch)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    comments = root / "comments.jsonl"
    comments.write_text(
        json.dumps(
            {"platform": "generic", "kind": "comment", "native_id": "c1", "text": "留言"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    imported = run(ctx, request(project, [local(comments, "jsonl")], ["comments"], key="item"))
    item_id = data(imported)["rows"][0]["item_id"]
    captions = root / "captions.vtt"
    captions.write_text(
        "WEBVTT\nLanguage: zh-Hant\n\n"
        "00:00:01.000 --> 00:00:04.000 align:start\n"
        "這是自製字幕，不是真實活動或使用者資料。\n"
        "\n"
        "00:00:03.000 --> 00:00:05.000\n"
        "重疊的第二句\n"
        "\n"
        "bad stamp\n",
        encoding="utf-8",
    )
    job = run(
        ctx,
        request(
            project,
            [local(captions, "vtt")],
            ["subtitles"],
            key="caps",
            attach_item_id=item_id,
            language="zh-Hant",
        ),
    )
    assert job.state == "partial" and job.stop_reason == "row_errors"
    committed = [row for row in data(job)["rows"] if row["status"] == "committed"]
    rejected = [row for row in data(job)["rows"] if row["status"] == "rejected"]
    assert len(committed) == 2 and len(rejected) == 1
    assert rejected[0]["field"] == "timestamp"
    assert committed[0]["message"] == "Cue overlaps another cue"
    segments = q(ctx, "SELECT * FROM transcript_segments ORDER BY cue_index")
    assert len(segments) == 2
    assert segments[0]["text"] == "這是自製字幕，不是真實活動或使用者資料。"
    assert segments[0]["language"] == "zh-Hant"
    assert segments[0]["method"] == "provided" and segments[0]["review"] == "unreviewed"
    assert segments[0]["item_id"] == item_id and segments[0]["asset_id"] is None
    assert segments[0]["project_id"] == project
    assert bool(segments[0]["overlap"]) is True
    assert segments[0]["start_ms"] == 1000 and segments[0]["end_ms"] == 4000
    source_id = segments[0]["source_id"]
    again = run(
        ctx,
        request(
            project,
            [local(captions, "vtt")],
            ["subtitles"],
            key="caps-again",
            attach_item_id=item_id,
            language="zh-Hant",
        ),
    )
    assert all(
        row["status"] == "unchanged" or row["status"] == "rejected" for row in data(again)["rows"]
    )
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 2
    other_item = run(
        ctx,
        request(
            project,
            [m.TextInput(kind="text", text="另一則")],
            ["post"],
            key="other-item",
        ),
    )
    other_id = data(other_item)["rows"][0]["item_id"]
    attached = run(
        ctx,
        request(
            project,
            [local(captions, "vtt")],
            ["subtitles"],
            key="caps-other",
            attach_item_id=other_id,
        ),
    )
    assert any(row["status"] == "committed" for row in data(attached)["rows"])
    assert (
        q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments WHERE item_id=?", (other_id,))[0]["n"]
        == 2
    )
    sources = {
        row["source_id"] for row in q(ctx, "SELECT DISTINCT source_id FROM transcript_segments")
    }
    assert source_id in sources and len(sources) == 2
    header = root / "header.vtt"
    header.write_text("WEBVTT\n\n", encoding="utf-8")
    empty = run(ctx, request(project, [local(header, "vtt")], ["subtitles"], key="header"))
    assert empty.state == "succeeded" and empty.stop_reason == "empty_input"
    page = root / "page.vtt"
    page.write_text("<html><body>not a caption track</body></html>", encoding="utf-8")
    html = run(ctx, request(project, [local(page, "vtt")], ["subtitles"], key="html-vtt"))
    assert html.state == "partial" and html.stop_reason == "parser_failed"
    assert data(html)["inputs"][0]["code"] == "E_UNSUPPORTED"
    assert html.coverage_status == "partial"
    fixture = FIXTURES / "captions.vtt"
    allow(ctx, root, FIXTURES)
    sample = run(
        ctx,
        request(
            project,
            [local(fixture, "vtt")],
            ["subtitles"],
            key="fixture-vtt",
            language="en",
        ),
    )
    assert sample.state == "succeeded"
    sample_row = q(ctx, "SELECT text, language FROM transcript_segments WHERE language='en'")[0]
    assert sample_row["text"] == "這是自製字幕，不是真實活動或使用者資料。"


def test_subtitle_does_not_cross_projects(ctx, project, tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = root / "one.srt"
    path.write_text("1\n00:00:00,000 --> 00:00:01,000\n僅此專案\n", encoding="utf-8")
    run(ctx, request(project, [local(path, "srt")], ["subtitles"], key="srt-a"))
    other = app.create_project(ctx, {"name": "Other", "request_key": "other-sub"}).data["id"]
    run(ctx, request(other, [local(path, "srt")], ["subtitles"], key="srt-b"))
    assert (
        q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments WHERE project_id=?", (project,))[0][
            "n"
        ]
        == 1
    )
    assert (
        q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments WHERE project_id=?", (other,))[0]["n"]
        == 1
    )
    leaked = q(
        ctx,
        "SELECT text FROM transcript_segments WHERE project_id=? AND text=?",
        (other, "僅此專案"),
    )
    assert len(leaked) == 1
    missing = run(
        ctx,
        request(
            project,
            [local(path, "srt")],
            ["subtitles"],
            key="missing-item",
            attach_item_id="itm_does_not_exist",
        ),
    )
    assert data(missing)["inputs"][0]["code"] == "E_REFERENCE_INVALID"
    assert (
        q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments WHERE project_id=?", (project,))[0][
            "n"
        ]
        == 1
    )


def _ffprobe_script(path, body: str):
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def test_rr_m2_hidden_ffprobe_stays_unavailable(ctx, project, tmp_path, monkeypatch):
    empty = tmp_path / "bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert shutil.which("ffprobe") is None
    calls = {"n": 0}

    def hidden(name: str):
        calls["n"] += 1
        assert name == "ffprobe"
        return None

    monkeypatch.setattr(media.shutil, "which", hidden)
    root = tmp_path / "imports"
    root.mkdir()
    allow(ctx, root)
    path = root / "tone.wav"
    path.write_bytes(wav_bytes())
    job = run(ctx, request(project, [local(path, "media")], ["media_file"], key="hidden-probe"))
    assert job.state == "succeeded" and job.error is None
    assert calls["n"] >= 1
    assets = q(ctx, "SELECT state FROM assets")
    assert len(assets) == 1 and assets[0]["state"] == "unavailable"
    assert assets[0]["state"] != "ready"
    basis = json.loads(q(ctx, "SELECT use_basis FROM sources")[0]["use_basis"])
    assert basis["probe"] == "not_installed"


def test_rr_l1_ffprobe_does_not_inherit_ffreport(tmp_path, monkeypatch):
    report = tmp_path / "stray_probe.log"
    env_sink = tmp_path / "child-env.txt"
    script = tmp_path / "ffprobe"
    _ffprobe_script(
        script,
        "#!/bin/sh\n"
        f'if [ -n "$FFREPORT" ]; then echo leaked > {shlex.quote(str(report))}; fi\n'
        f"env > {shlex.quote(str(env_sink))}\n"
        'printf \'%s\' \'{"format":{"duration":"1.0"},'
        '"streams":[{"codec_type":"audio","codec_name":"pcm_s16le"}]}\'\n',
    )
    monkeypatch.setenv("FFREPORT", f"file={report}:level=32")
    monkeypatch.setenv("AV_LOG_FORCE_NOCOLOR", "1")
    monkeypatch.setenv("FFMPEG_DATADIR", str(tmp_path / "ffmpeg-data"))

    def hidden(name: str):
        return str(script) if name == "ffprobe" else None

    monkeypatch.setattr(media.shutil, "which", hidden)
    seen = {}
    real_popen = media.subprocess.Popen

    def spy(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["kwargs"] = kwargs
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(media.subprocess, "Popen", spy)
    wav = tmp_path / "tone.wav"
    wav.write_bytes(wav_bytes())
    status, duration = media.probe_media(wav, timeout=5)
    assert status == "probed" and duration == 1000
    assert not report.exists()
    child = env_sink.read_text(encoding="utf-8")
    assert "FFREPORT=" not in child
    assert "AV_LOG_" not in child
    assert "FFMPEG_" not in child
    assert "PATH=/usr/bin:/bin\n" in child or child.strip() == "PATH=/usr/bin:/bin"
    assert os.environ["FFREPORT"].startswith("file=")
    assert os.path.isabs(seen["argv"][0])
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["stdin"] is media.subprocess.DEVNULL
    assert seen["kwargs"]["env"] == {"PATH": "/usr/bin:/bin"}
    assert seen["kwargs"]["start_new_session"] is True
    assert seen["kwargs"]["bufsize"] == 0
    real = shutil.which("ffprobe")
    if real is None:
        return
    real_report = tmp_path / "real-stray.log"
    monkeypatch.setenv("FFREPORT", f"file={real_report}:level=32")

    def installed(name: str):
        return real if name == "ffprobe" else None

    monkeypatch.setattr(media.shutil, "which", installed)
    status, _duration = media.probe_media(wav, timeout=15)
    assert status == "probed"
    assert not real_report.exists()
    assert not list(tmp_path.glob("*.log"))
    assert not (tmp_path / "stray_probe.log").exists()
    assert not os.path.exists("stray_probe.log")


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_rr_l1_over_cap_probe_output_is_a_fixed_error(tmp_path, monkeypatch, stream):
    pidfile = tmp_path / "pid"
    script = tmp_path / "ffprobe"
    redirect = "" if stream == "stdout" else " >&2"
    # Keep writing so a reader that waits for process exit would hit the timeout.
    _ffprobe_script(
        script,
        "#!/bin/sh\n"
        f"echo $$ > {shlex.quote(str(pidfile))}\n"
        "while true; do\n"
        f"  head -c 65536 /dev/zero{redirect} || exit 1\n"
        "done\n",
    )

    def hidden(name: str):
        return str(script) if name == "ffprobe" else None

    monkeypatch.setattr(media.shutil, "which", hidden)
    wav = tmp_path / "tone.wav"
    wav.write_bytes(wav_bytes())
    started = time.monotonic()
    with pytest.raises(m.CoreError) as error:
        media.probe_media(wav, timeout=5)
    assert time.monotonic() - started < 4
    assert error.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert error.value.detail.message == "Media probe output exceeded the byte limit"
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        raise AssertionError(f"probe process {pid} still running")
