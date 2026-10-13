#!/usr/bin/env python3
"""Media fixture generator for MetaMuse CLI synthetic test corpus.

Generates tiny synthetic media files (<64 KiB each, <400 KiB total) using the
system ffmpeg with fixed argv lists (no shell), and regenerates the oversized
JSONL row in import/comments_mixed_bad.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def generate_media(corpus_dir: Path) -> dict[str, dict[str, int | str]]:
    media_dir = corpus_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    tiny_mp4 = media_dir / "tiny.mp4"
    tiny_wav = media_dir / "tiny.wav"
    tiny_png = media_dir / "tiny.png"
    truncated_mp4 = media_dir / "truncated.mp4"
    zero_bytes_mp4 = media_dir / "zero_bytes.mp4"
    html_as_mp4 = media_dir / "html_as.mp4"
    same_bytes_a = media_dir / "same_bytes_a.mp4"
    same_bytes_b = media_dir / "same_bytes_b.mp4"

    # 1. 1-second tiny MP4 (testsrc + sine, 64x64, 10fps, libx264 + aac)
    cmd_mp4 = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=64x64:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "32k",
        str(tiny_mp4),
    ]
    subprocess.run(
        cmd_mp4,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 2. 1-second tiny WAV (sine 440Hz, 8000Hz mono PCM)
    cmd_wav = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "8000",
        "-ac",
        "1",
        str(tiny_wav),
    ]
    subprocess.run(
        cmd_wav,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 3. Tiny PNG (16x16 blue color frame)
    cmd_png = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=16x16:d=1",
        "-frames:v",
        "1",
        "-update",
        "1",
        str(tiny_png),
    ]
    subprocess.run(
        cmd_png,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 4. Truncated MP4 (first half of tiny.mp4 bytes)
    mp4_bytes = tiny_mp4.read_bytes()
    truncated_mp4.write_bytes(mp4_bytes[: len(mp4_bytes) // 2])

    # 5. Zero-byte MP4
    zero_bytes_mp4.write_bytes(b"")

    # 6. HTML bytes disguised as MP4
    html_as_mp4.write_bytes(
        b"<!DOCTYPE html><html><head><title>Disguised HTML</title></head>"
        b"<body><p>This is HTML content disguised with an .mp4 extension.</p></body></html>"
    )

    # 7. Byte-identical copies (same_bytes_a.mp4 and same_bytes_b.mp4)
    same_bytes_a.write_bytes(mp4_bytes)
    same_bytes_b.write_bytes(mp4_bytes)

    # Verification and hash recording
    results = {}
    total_size = 0
    all_media = [
        tiny_mp4,
        tiny_wav,
        tiny_png,
        truncated_mp4,
        zero_bytes_mp4,
        html_as_mp4,
        same_bytes_a,
        same_bytes_b,
    ]
    for mf in all_media:
        data = mf.read_bytes()
        sz = len(data)
        total_size += sz
        h = hashlib.sha256(data).hexdigest()
        assert sz < 65536, f"File {mf.name} exceeds 64 KiB limit: {sz} bytes"
        results[mf.name] = {"size": sz, "sha256": h}

    assert total_size < 409600, f"Total media size exceeds 400 KiB: {total_size} bytes"
    return results


def regenerate_oversized_row(corpus_dir: Path) -> None:
    """Generate or update import/comments_mixed_bad.jsonl including > 1 MiB text field."""
    import_dir = corpus_dir / "import"
    import_dir.mkdir(parents=True, exist_ok=True)
    target_file = import_dir / "comments_mixed_bad.jsonl"

    # Oversized text > 1 MiB (1,048,576 bytes)
    # 70,000 repeats of a 33-byte UTF-8 string = 2,310,000 bytes (~2.2 MiB)
    oversized_chunk = "測試超長文字欄位超過1MiB。"
    oversized_text = oversized_chunk * 70000

    lines = [
        # Line 1: Valid
        json.dumps(
            {
                "platform": "generic",
                "kind": "comment",
                "native_id": "bad-mix-valid-01",
                "native_scope": None,
                "parent_native_id": None,
                "text": "正常留言第一行，測試逐項驗證。",
                "source_url": "https://example.org/c/1",
                "upstream_collection_method": "synthetic_fixture",
                "created_at_raw": "10分鐘前",
                "created_at": "2026-10-13T10:00:00Z",
                "author_display": "正常使用者甲",
                "media": None,
            },
            ensure_ascii=False,
        ),
        # Line 2: Invalid JSON syntax (E_INPUT_INVALID)
        '{"platform": "generic", "kind": "comment", "native_id": "syntax-err-01", text: unquoted_value_error}',
        # Line 3: Valid
        json.dumps(
            {
                "platform": "generic",
                "kind": "comment",
                "native_id": "bad-mix-valid-02",
                "native_scope": None,
                "parent_native_id": None,
                "text": "正常留言第二行，緊接在語法錯誤之後。",
                "source_url": "https://example.org/c/2",
                "upstream_collection_method": "synthetic_fixture",
                "created_at_raw": "9分鐘前",
                "created_at": "2026-10-13T10:01:00Z",
                "author_display": "正常使用者乙",
                "media": None,
            },
            ensure_ascii=False,
        ),
        # Line 4: Wrong type - kind is integer 12345 (E_INPUT_INVALID)
        '{"platform": "generic", "kind": 12345, "native_id": "wrong-type-01", "native_scope": null, "parent_native_id": null, "text": "kind型別錯誤為整數", "source_url": "https://example.org/c/3", "upstream_collection_method": "synthetic_fixture", "created_at_raw": "8分鐘前", "created_at": "2026-10-13T10:02:00Z", "author_display": "型別錯誤者", "media": null}',
        # Line 5: Valid
        json.dumps(
            {
                "platform": "generic",
                "kind": "comment",
                "native_id": "bad-mix-valid-03",
                "native_scope": None,
                "parent_native_id": None,
                "text": "正常留言第三行，型別錯誤之後仍能正常讀取。",
                "source_url": "https://example.org/c/4",
                "upstream_collection_method": "synthetic_fixture",
                "created_at_raw": "7分鐘前",
                "created_at": "2026-10-13T10:03:00Z",
                "author_display": "正常使用者丙",
                "media": None,
            },
            ensure_ascii=False,
        ),
        # Line 6: Unknown kind (E_UNSUPPORTED)
        '{"platform": "generic", "kind": "unsupported_broadcast", "native_id": "unknown-kind-01", "native_scope": null, "parent_native_id": null, "text": "未支援的 kind 欄位值", "source_url": "https://example.org/c/5", "upstream_collection_method": "synthetic_fixture", "created_at_raw": "6分鐘前", "created_at": "2026-10-13T10:04:00Z", "author_display": "未知類別者", "media": null}',
        # Line 7: Valid
        json.dumps(
            {
                "platform": "generic",
                "kind": "comment",
                "native_id": "bad-mix-valid-04",
                "native_scope": None,
                "parent_native_id": None,
                "text": "正常留言第四行，未知類別之後不受影響。",
                "source_url": "https://example.org/c/6",
                "upstream_collection_method": "synthetic_fixture",
                "created_at_raw": "5分鐘前",
                "created_at": "2026-10-13T10:05:00Z",
                "author_display": "正常使用者丁",
                "media": None,
            },
            ensure_ascii=False,
        ),
        # Line 8: Missing required field (platform) (E_INPUT_INVALID)
        '{"kind": "comment", "native_id": "missing-field-01", "native_scope": null, "parent_native_id": null, "text": "缺少 platform 必填欄位", "created_at_raw": "4分鐘前", "created_at": "2026-10-13T10:06:00Z", "author_display": "缺欄位者", "media": null}',
        # Line 9: Valid
        json.dumps(
            {
                "platform": "generic",
                "kind": "comment",
                "native_id": "bad-mix-valid-05",
                "native_scope": None,
                "parent_native_id": None,
                "text": "正常留言第五行，缺少欄位之後。",
                "source_url": "https://example.org/c/7",
                "upstream_collection_method": "synthetic_fixture",
                "created_at_raw": "3分鐘前",
                "created_at": "2026-10-13T10:07:00Z",
                "author_display": "正常使用者戊",
                "media": None,
            },
            ensure_ascii=False,
        ),
        # Line 10: Oversized text field > 1 MiB (E_LIMIT_REACHED)
        json.dumps(
            {
                "platform": "generic",
                "kind": "comment",
                "native_id": "bad-oversized-01",
                "native_scope": None,
                "parent_native_id": None,
                "text": oversized_text,
                "source_url": "https://example.org/c/8",
                "upstream_collection_method": "synthetic_fixture",
                "created_at_raw": "2分鐘前",
                "created_at": "2026-10-13T10:08:00Z",
                "author_display": "大文字測試者",
                "media": None,
            },
            ensure_ascii=False,
        ),
        # Line 11: Valid
        json.dumps(
            {
                "platform": "generic",
                "kind": "comment",
                "native_id": "bad-mix-valid-06",
                "native_scope": None,
                "parent_native_id": None,
                "text": "正常留言第六行，大欄位之後。",
                "source_url": "https://example.org/c/9",
                "upstream_collection_method": "synthetic_fixture",
                "created_at_raw": "2分鐘前",
                "created_at": "2026-10-13T10:09:00Z",
                "author_display": "正常使用者己",
                "media": None,
            },
            ensure_ascii=False,
        ),
        # Line 12: NaN literal (E_INPUT_INVALID)
        '{"platform": "generic", "kind": "comment", "native_id": "bad-nan-01", "native_scope": null, "parent_native_id": null, "text": "含有NaN數值", "source_url": "https://example.org/c/10", "upstream_collection_method": "synthetic_fixture", "score": NaN, "created_at_raw": "1分鐘前", "created_at": "2026-10-13T10:10:00Z", "author_display": "NaN測試者", "media": null}',
        # Line 13: Infinity literal (E_INPUT_INVALID)
        '{"platform": "generic", "kind": "comment", "native_id": "bad-inf-01", "native_scope": null, "parent_native_id": null, "text": "含有Infinity數值", "source_url": "https://example.org/c/11", "upstream_collection_method": "synthetic_fixture", "score": Infinity, "created_at_raw": "剛剛", "created_at": "2026-10-13T10:11:00Z", "author_display": "Infinity測試者", "media": null}',
        # Line 14: Valid
        json.dumps(
            {
                "platform": "generic",
                "kind": "comment",
                "native_id": "bad-mix-valid-07",
                "native_scope": None,
                "parent_native_id": None,
                "text": "正常留言第七行，結尾資料完好無損。",
                "source_url": "https://example.org/c/12",
                "upstream_collection_method": "synthetic_fixture",
                "created_at_raw": "剛剛",
                "created_at": "2026-10-13T10:12:00Z",
                "author_display": "正常使用者庚",
                "media": None,
            },
            ensure_ascii=False,
        ),
    ]

    target_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    corpus_dir = Path(__file__).resolve().parent
    print(f"Generating media in {corpus_dir / 'media'}...")
    media_info = generate_media(corpus_dir)
    for name, info in media_info.items():
        print(f"  {name}: {info['size']} bytes, sha256={info['sha256']}")

    print("Regenerating oversized row in import/comments_mixed_bad.jsonl...")
    regenerate_oversized_row(corpus_dir)
    print("Media and mixed bad data generation complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
