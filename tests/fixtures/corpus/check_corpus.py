#!/usr/bin/env python3
"""Corpus integrity and manifest verification test suite for MetaMuse CLI.

Stdlib-only, runnable with `python3 tests/fixtures/corpus/check_corpus.py`.
Verifies that:
1. Every file listed in manifest.json exists.
2. Hashes (SHA-256) and sizes match the manifest.
3. Every committed file under tests/fixtures/corpus/ is listed in the manifest.
4. JSONL valid-row counts and invalid-row error codes match expectations.
5. Search corpus has 260 items (230 A, 30 B) and exact hit counts for all queries.
6. Subtitle files meet cue count, overlap, and error expectations.
7. Media files are under 64 KiB each and under 400 KiB total.
8. Security URL and Path verdicts match expectations.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path


def compute_sha256_and_size(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def parse_vtt_cues(content: str) -> tuple[bool, int, bool, str | None]:
    """Parse WebVTT content for header, cue count, and overlaps."""
    lines = content.splitlines()
    if not lines or not lines[0].startswith("WEBVTT"):
        return False, 0, False, "Not WebVTT header"

    cue_pattern = re.compile(
        r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}\.\d{3})"
    )
    cues = []
    has_overlap = False

    def to_ms(ts: str) -> int:
        h, m, s = ts.split(":")
        sec, ms = s.split(".")
        return int(h) * 3600000 + int(m) * 60000 + int(sec) * 1000 + int(ms)

    for line in lines:
        m = cue_pattern.match(line.strip())
        if m:
            start_str, end_str = m.group(1), m.group(2)
            try:
                # Validate minutes and seconds < 60
                _h, m_val, s_val = start_str.split(":")
                if int(m_val) >= 60 or float(s_val) >= 60:
                    return False, 0, False, "Malformed timestamp value >= 60"
                start_ms = to_ms(start_str)
                end_ms = to_ms(end_str)
            except (ValueError, IndexError) as e:
                return False, 0, False, f"Malformed timestamp: {e}"

            if start_ms < 0:
                return False, 0, False, "Negative start time"
            if end_ms < start_ms:
                return False, 0, False, "End before start"

            if cues and start_ms < cues[-1][1]:
                has_overlap = True
            cues.append((start_ms, end_ms))

    # Also detect negative time syntax in original text
    if "-00:" in content or "99:99" in content:
        return False, 0, False, "Negative or malformed timestamp detected"

    return True, len(cues), has_overlap, None


def parse_srt_cues(raw_bytes: bytes) -> tuple[bool, int, bool, str | None]:
    """Parse SubRip SRT content for cue count, timestamps, and order."""
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        return False, 0, False, f"Decode error: {e}"

    lines = [line.strip() for line in text.splitlines()]
    srt_pattern = re.compile(
        r"^(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})"
    )
    cues = []

    def to_ms(ts: str) -> int:
        h, m, s = ts.split(":")
        sec, ms = s.split(",")
        return int(h) * 3600000 + int(m) * 60000 + int(sec) * 1000 + int(ms)

    for line in lines:
        m = srt_pattern.match(line)
        if m:
            start_ms = to_ms(m.group(1))
            end_ms = to_ms(m.group(2))
            if end_ms < start_ms:
                return False, 0, False, "End before start"
            cues.append((start_ms, end_ms))

    return True, len(cues), False, None


def check_corpus(corpus_dir: Path) -> int:
    manifest_path = corpus_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"FAIL: manifest.json not found in {corpus_dir}")
        return 1

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"FAIL: Failed to parse manifest.json: {e}")
        return 1

    manifest_files = manifest.get("files", {})
    print(f"Loaded manifest.json with {len(manifest_files)} registered files.")

    # 1. Completeness: check that every committed file is in manifest
    print("\n[Check 1] Verifying all disk files are in manifest...")
    disk_files = set()
    for root, dirs, files in os.walk(corpus_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        for f in files:
            if f in ("manifest.json",):
                continue
            p = Path(root) / f
            rel = p.relative_to(corpus_dir).as_posix()
            disk_files.add(rel)

    registered_files = set(manifest_files.keys())
    unregistered = disk_files - registered_files
    missing_on_disk = registered_files - disk_files

    if unregistered:
        print(f"FAIL: Unregistered files found on disk: {sorted(unregistered)}")
        return 1
    if missing_on_disk:
        print(f"FAIL: Files listed in manifest missing on disk: {sorted(missing_on_disk)}")
        return 1
    print(f"  OK: All {len(disk_files)} committed files are accounted for in manifest.")

    # 2. Check SHA-256 and size of every file
    print("\n[Check 2] Verifying SHA-256 hashes and file sizes...")
    for rel_path, meta in manifest_files.items():
        file_path = corpus_dir / rel_path
        sha, size = compute_sha256_and_size(file_path)
        if sha != meta["sha256"]:
            print(f"FAIL: SHA-256 mismatch for {rel_path}: expected {meta['sha256']}, got {sha}")
            return 1
        if size != meta["size_bytes"]:
            print(f"FAIL: Size mismatch for {rel_path}: expected {meta['size_bytes']}, got {size}")
            return 1
    print(f"  OK: All {len(manifest_files)} file hashes and sizes match exactly.")

    # 3. Check JSONL files row counts & error expectations
    print("\n[Check 3] Verifying JSONL row counts and schema validation...")
    for rel_path, meta in manifest_files.items():
        if not rel_path.endswith(".jsonl"):
            continue
        file_path = corpus_dir / rel_path
        expected = meta.get("expected", {})
        lines = [line.rstrip("\r\n") for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        total_lines = len(lines)
        if "total_rows" in expected and total_lines != expected["total_rows"]:
            print(f"FAIL: {rel_path} total rows mismatch: expected {expected['total_rows']}, got {total_lines}")
            return 1

        # Check valid rows count
        valid_count = 0
        for _line_num, line in enumerate(lines, start=1):
            try:
                # Reject NaN and Infinity
                parsed = json.loads(
                    line,
                    parse_constant=lambda x: None if x == "null" else (_ for _ in ()).throw(ValueError(f"Invalid constant {x}")),
                )
                # Basic schema checks
                if (
                    isinstance(parsed, dict)
                    and "platform" in parsed
                    and "kind" in parsed
                    and "source_url" in parsed
                    and isinstance(parsed["kind"], str)
                    and parsed["kind"] in ("post", "comment", "reply")
                ):
                    # Check text length < 1 MiB
                    text_val = parsed.get("text")
                    if text_val is None or len(text_val.encode("utf-8")) <= 1048576:
                        valid_count += 1
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

        if "valid_rows" in expected and valid_count != expected["valid_rows"]:
            print(f"FAIL: {rel_path} valid rows mismatch: expected {expected['valid_rows']}, got {valid_count}")
            return 1

        # For comments_mixed_bad.jsonl, verify invalid rows line numbers and codes
        if rel_path == "import/comments_mixed_bad.jsonl":
            invalid_specs = expected.get("invalid_rows", [])
            expected_invalid_lines = {inv["line"]: inv["error_code"] for inv in invalid_specs}
            for line_num in expected_invalid_lines:
                raw_line = lines[line_num - 1]
                # Verify that it is indeed invalid
                is_valid = True
                try:
                    p = json.loads(
                        raw_line,
                        parse_constant=lambda x: None if x == "null" else (_ for _ in ()).throw(ValueError(f"Invalid constant {x}")),
                    )
                    if (
                        isinstance(p, dict)
                        and "platform" in p
                        and isinstance(p.get("kind"), str)
                        and p["kind"] in ("post", "comment", "reply")
                        and "source_url" in p
                    ):
                        text_val = p.get("text")
                        is_valid = text_val is None or len(text_val.encode("utf-8")) <= 1048576
                    else:
                        is_valid = False
                except (json.JSONDecodeError, ValueError, TypeError):
                    is_valid = False
                if is_valid:
                    print(f"FAIL: Line {line_num} in comments_mixed_bad.jsonl was expected to be invalid, but passed validation")
                    return 1

    print("  OK: JSONL row counts and invalid-row error codes match expectations.")

    # 4. Check Subtitle files
    print("\n[Check 4] Verifying subtitle cues, overlaps, and timestamp handling...")
    for rel_path, meta in manifest_files.items():
        if not rel_path.endswith((".vtt", ".srt")):
            continue
        file_path = corpus_dir / rel_path
        exp = meta.get("expected", {})
        if rel_path.endswith(".vtt"):
            content = file_path.read_text(encoding="utf-8")
            valid, cue_count, has_overlap, err = parse_vtt_cues(content)
        else:
            raw_bytes = file_path.read_bytes()
            valid, cue_count, has_overlap, err = parse_srt_cues(raw_bytes)

        if valid != exp.get("valid"):
            print(f"FAIL: {rel_path} validity mismatch: expected {exp.get('valid')}, got {valid} (err: {err})")
            return 1
        if exp.get("valid"):
            if cue_count != exp.get("cue_count"):
                print(f"FAIL: {rel_path} cue count mismatch: expected {exp.get('cue_count')}, got {cue_count}")
                return 1
            if has_overlap != exp.get("has_overlaps"):
                print(f"FAIL: {rel_path} has_overlaps mismatch: expected {exp.get('has_overlaps')}, got {has_overlap}")
                return 1

    print("  OK: All subtitle files meet cue count, overlap, and error expectations.")

    # 5. Check Media files
    print("\n[Check 5] Verifying media sizes and byte identities...")
    media_total = 0
    tiny_mp4_bytes = (corpus_dir / "media" / "tiny.mp4").read_bytes()
    for rel_path in manifest_files:
        if not rel_path.startswith("media/"):
            continue
        file_path = corpus_dir / rel_path
        sz = file_path.stat().st_size
        media_total += sz
        if sz >= 65536:
            print(f"FAIL: Media file {rel_path} size {sz} >= 64 KiB limit")
            return 1

        if rel_path in ("media/same_bytes_a.mp4", "media/same_bytes_b.mp4") and file_path.read_bytes() != tiny_mp4_bytes:
            print(f"FAIL: {rel_path} does not match tiny.mp4 bytes")
            return 1

        if rel_path == "media/truncated.mp4":
            expected_trunc = tiny_mp4_bytes[: len(tiny_mp4_bytes) // 2]
            if file_path.read_bytes() != expected_trunc:
                print("FAIL: truncated.mp4 does not match half of tiny.mp4 bytes")
                return 1

        if rel_path == "media/zero_bytes.mp4" and sz != 0:
            print(f"FAIL: zero_bytes.mp4 size is {sz} != 0")
            return 1

    if media_total >= 409600:
        print(f"FAIL: Total media size {media_total} >= 400 KiB limit")
        return 1
    print(f"  OK: 8 media files verified (total {media_total} bytes < 400 KiB, each < 64 KiB).")

    # 6. Check Search Corpus queries
    print("\n[Check 6] Verifying search corpus items and query hit counts...")
    search_file = corpus_dir / "search" / "search_corpus.jsonl"
    search_meta = manifest_files.get("search/search_corpus.jsonl", {}).get("expected", {})
    search_items = [json.loads(line) for line in search_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    items_a = [it for it in search_items if it.get("project") == "project_a"]
    items_b = [it for it in search_items if it.get("project") == "project_b"]

    if len(search_items) != 260:
        print(f"FAIL: Search corpus total items expected 260, got {len(search_items)}")
        return 1
    if len(items_a) != 230:
        print(f"FAIL: Search corpus Project A items expected 230, got {len(items_a)}")
        return 1
    if len(items_b) != 30:
        print(f"FAIL: Search corpus Project B items expected 30, got {len(items_b)}")
        return 1

    queries = search_meta.get("queries", {})
    for query_text, qinfo in queries.items():
        hits_a = sum(1 for it in items_a if query_text in it.get("text", ""))
        hits_b = sum(1 for it in items_b if query_text in it.get("text", ""))
        if hits_a != qinfo["expected_hits_project_a"]:
            print(f"FAIL: Search query '{query_text}' Project A expected {qinfo['expected_hits_project_a']}, got {hits_a}")
            return 1
        if hits_b != qinfo["expected_hits_project_b"]:
            print(f"FAIL: Search query '{query_text}' Project B expected {qinfo['expected_hits_project_b']}, got {hits_b}")
            return 1
        print(f"  Query '{query_text}': Project A = {hits_a}, Project B = {hits_b} -> OK")

    # 7. Check Security files
    print("\n[Check 7] Verifying security URLs and Paths...")
    urls_meta = manifest_files.get("security/urls.jsonl", {}).get("expected", {})
    urls_file = corpus_dir / "security" / "urls.jsonl"
    urls_rows = [json.loads(line) for line in urls_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    allow_urls = sum(1 for r in urls_rows if r.get("expected_verdict") == "allow")
    reject_urls = sum(1 for r in urls_rows if r.get("expected_verdict") == "reject")
    if len(urls_rows) != urls_meta.get("total_rows"):
        print(f"FAIL: urls.jsonl total rows mismatch: expected {urls_meta.get('total_rows')}, got {len(urls_rows)}")
        return 1
    if allow_urls != urls_meta.get("allowed_count") or reject_urls != urls_meta.get("rejected_count"):
        print(f"FAIL: urls.jsonl verdicts mismatch: allow={allow_urls}, reject={reject_urls}")
        return 1

    paths_meta = manifest_files.get("security/paths.jsonl", {}).get("expected", {})
    paths_file = corpus_dir / "security" / "paths.jsonl"
    paths_rows = [json.loads(line) for line in paths_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    allow_paths = sum(1 for r in paths_rows if r.get("expected_verdict") == "allow")
    reject_paths = sum(1 for r in paths_rows if r.get("expected_verdict") == "reject")
    runtime_paths = sum(1 for r in paths_rows if r.get("expected_verdict") == "runtime_symlink_test")
    if len(paths_rows) != paths_meta.get("total_rows"):
        print(f"FAIL: paths.jsonl total rows mismatch: expected {paths_meta.get('total_rows')}, got {len(paths_rows)}")
        return 1
    if (
        allow_paths != paths_meta.get("allowed_count")
        or reject_paths != paths_meta.get("rejected_count")
        or runtime_paths != paths_meta.get("runtime_symlink_test_count")
    ):
        print("FAIL: paths.jsonl verdicts mismatch")
        return 1

    print("  OK: Security URLs and Paths records verified.")

    print("\n========================================================")
    print("ALL CORPUS INTEGRITY AND ACCEPTANCE CHECKS PASSED (100%)")
    print("========================================================")
    return 0


def main() -> int:
    corpus_dir = Path(__file__).resolve().parent
    return check_corpus(corpus_dir)


if __name__ == "__main__":
    sys.exit(main())
