"""Reproducible B07 first-page search measurement; synthetic data, disk temp only."""

import hashlib
import json
import math
import os
import platform
import resource
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

from metamuse import app
from metamuse import models as m


def main() -> None:
    task_tmp = Path.home() / "agent-work" / "tmp" / "content-search-measure"
    task_tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
    corpus_hash = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix="b07-", dir=task_tmp) as temporary:
        ctx = app.AppContext.open(Path(temporary) / "workspace")
        project = app.create_project(ctx, {"name": "Synthetic measure", "request_key": "one"}).data[
            "id"
        ]
        with ctx.store.transaction() as conn:
            ctx.store.insert(
                m.Source(
                    id="src_measure",
                    project_id=project,
                    platform="generic",
                    collection_method="file_import",
                    fetched_at="2026-10-13T00:00:00+00:00",
                ),
                conn=conn,
            )
            for i in range(10_000):
                iid = f"itm_measure_{i:05d}"
                text = f"繁體中文活動 {i:05d} 😀\r\n" + "固定合成樣本，保留來源與換行。" * 20
                corpus_hash.update(text.encode("utf-8"))
                ctx.store.insert(
                    m.Item(
                        id=iid,
                        project_id=project,
                        source_id="src_measure",
                        kind="comment",
                    ),
                    conn=conn,
                )
                ctx.store.insert(
                    m.ItemRevision(
                        item_id=iid,
                        revision=1,
                        text=text,
                        text_hash=m.payload_digest(text),
                        fetched_at="2026-10-13T00:00:00+00:00",
                        complete=True,
                    ),
                    conn=conn,
                )
        samples = []
        for _ in range(30):
            started = time.perf_counter()
            result = app.search_evidence(ctx, {"project_id": project, "query": "中", "limit": 50})
            elapsed = time.perf_counter() - started
            if result.status != "ok" or len(result.data["results"]) != 50 or not result.next_cursor:
                raise RuntimeError("Measurement did not yield a successful first page")
            samples.append(elapsed * 1000)
        ordered = sorted(samples)
        result = {
            "evidence_class": "MEASURE",
            "rows": 10000,
            "runs": len(samples),
            "query": "中",
            "matching_rows": 10000,
            "first_page_size": 50,
            "corpus_sha256": corpus_hash.hexdigest(),
            "timing_scope": "Full application call including fresh snapshot and cursor disk writes",
            "p95_method": "nearest rank, sorted sample ceil(0.95 * 30) = 29",
            "min_ms": min(samples),
            "median_ms": statistics.median(samples),
            "p95_ms": ordered[math.ceil(0.95 * len(samples)) - 1],
            "max_ms": max(samples),
            "samples_ms": samples,
            "search_target_ms": 2000,
            "search_target_met": ordered[28] <= 2000,
            "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "environment": {
                "os": platform.system(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version,
                "cpu_affinity_count": len(os.sched_getaffinity(0)),
                "storage": "home-directory disk; no tmpfs; normal filesystem cache",
            },
            "deviations": [
                "Local host, not a controlled 4-vCPU/8-GiB/SSD reference environment",
                "No OS cache flushing; first sample included, no warm-up discarded",
                "RSS includes corpus setup; not the separate 20-MiB import RSS acceptance test",
                "Only search was measured; submit and import performance are outside this task",
            ],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
