"""Collect the normal suite, select an ordered cohort, and verify CI manifests.

Only pytest (already locked) is needed to run; verification uses the stdlib alone.
No test discovery patterns, product settings, or fixture inputs are changed here.
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

COHORTS = ("core", "muse", "media", "facebook", "privacy")
PARITY_TEST = "test_r9_fixture_differential_corpus"


def cohort_for(path: str) -> str:
    """Classify collected file paths; unfamiliar files always remain in core."""
    name = Path(path).stem
    if "facebook" in name or "facebook" in Path(path).parts:
        return "facebook"
    if "muse" in name:
        return "muse"
    if "remote" in name or "media" in name:
        return "media"
    if any(word in name for word in ("privacy", "redact", "safety", "secret", "keys", "approval")):
        return "privacy"
    return "core"


def node_hash(nodes: list[str]) -> str:
    body = json.dumps(sorted(nodes), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def unique_nodes(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(n, str) for n in value):
        raise ValueError(f"{label}: expected nonempty node IDs")
    if len(set(value)) != len(value):
        raise ValueError(f"{label}: duplicate node IDs")
    return value


def verify_manifests(directory: Path, source_sha: str) -> None:
    paths = sorted(directory.glob("*.json"))
    if {path.name for path in paths} != {f"{group}.json" for group in COHORTS}:
        raise ValueError("missing or unexpected cohort manifests")
    reference = None
    union: set[str] = set()
    counts = {}
    for path in paths:
        data = json.loads(path.read_text())
        group = path.stem
        if data["version"] != 1 or data["cohort"] != group or data["source_sha"] != source_sha:
            raise ValueError(f"{group}: manifest identity mismatch")
        if data["pytest_exit_code"] != 0 or data["collect_only"] is not False:
            raise ValueError(f"{group}: requires a successful runtime pytest exit")
        full = unique_nodes(data["full"], f"{group} full")
        if full != sorted(full) or data["full_hash"] != node_hash(full):
            raise ValueError(f"{group}: invalid full collection hash/order")
        if reference is None:
            reference = full
        if full != reference:
            raise ValueError(f"{group}: full collection mismatch")
        expected_counts = dict(Counter(cohort_for(n.split("::", 1)[0]) for n in full))
        if data["cohort_counts"] != expected_counts or set(expected_counts) != set(COHORTS):
            raise ValueError(f"{group}: invalid cohort counts or empty cohort")
        selected = unique_nodes(data["selected"], f"{group} selected")
        expected = {n for n in full if cohort_for(n.split("::", 1)[0]) == group}
        if set(selected) != expected:
            raise ValueError(f"{group}: selected nodes differ from the full partition")
        finished = unique_nodes(data["finished"], f"{group} finished")
        if set(finished) != set(selected):
            raise ValueError(f"{group}: incomplete runtime")
        if union.intersection(selected):
            raise ValueError(f"{group}: duplicate assignment across cohorts")
        union.update(selected)
        counts[group] = len(selected)
        check_parity_order(selected, group)
    if union != set(reference or []):
        raise ValueError("cohort union differs from full collection")
    print(f"Verified {source_sha}: {len(union)} nodes; sha256={node_hash(list(union))}")
    print(f"Cohort counts: {json.dumps(counts, sort_keys=True)}")


def check_parity_order(selected: list[str], group: str) -> None:
    parity = [n for n in selected if n.split("::")[-1] == PARITY_TEST]
    if parity and (group != "facebook" or len(parity) != 1 or selected[-1] != parity[0]):
        raise ValueError("Facebook fixture differential corpus must run last in facebook")


def run_cohort(group: str, manifest: Path, source_sha: str, pytest_args: list[str]) -> int:
    import pytest
    import pytest_diagnostics

    class Selection:
        def __init__(self):
            self.data = {
                "version": 1,
                "cohort": group,
                "source_sha": source_sha,
                "pytest_exit_code": None,
                "collect_only": None,
                "full": [],
                "full_hash": None,
                "cohort_counts": {},
                "selected": [],
                "finished": [],
            }

        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_collection_modifyitems(self, session, config, items):
            # Snapshot before any -k/-m/other plugin deselection. Unwind after
            # conftest's parity-last sort and all other collection hooks.
            full = [item.nodeid for item in items]
            self.data.update(full=sorted(full), full_hash=node_hash(full))
            self.data["collect_only"] = bool(config.option.collectonly)
            yield
            if session.testsfailed:
                # Let pytest report its original collection error/exit, rather
                # than replacing it with an empty-selection usage error.
                items.clear()
                return
            try:
                unique_nodes(full, "full collection")
                if Counter(item.nodeid for item in items) != Counter(full):
                    raise ValueError("pytest deselection would omit full-suite nodes")
                assignments = {
                    item.nodeid: cohort_for(item.path.relative_to(config.rootpath).as_posix())
                    for item in items
                }
                counts = dict(Counter(assignments.values()))
                self.data["cohort_counts"] = counts
                # Every production cohort must be present; tiny harness probes
                # can still exercise a single cohort, but aggregate rejects it.
                selected = [item for item in items if assignments[item.nodeid] == group]
                ids = unique_nodes([item.nodeid for item in selected], f"{group} selection")
                check_parity_order(ids, group)
            except ValueError as exc:
                raise pytest.UsageError(str(exc)) from exc
            deselected = [item for item in items if assignments[item.nodeid] != group]
            items[:] = selected
            session.config.hook.pytest_deselected(items=deselected)
            self.data["selected"] = ids
            # Keep collection evidence if the job deadline kills the process
            # before pytest returns. A null exit remains ineligible for success.
            manifest.write_text(json.dumps(self.data, indent=2) + "\n")
            reporter = config.pluginmanager.get_plugin("terminalreporter")
            if reporter:
                reporter.write_line(f"Full collection: {len(full)} nodes; sha256={node_hash(full)}")
                reporter.write_line(f"Cohort counts: {json.dumps(counts, sort_keys=True)}")
                reporter.write_line(f"Selected {group}: {len(ids)} nodes")
                for node in ids:
                    reporter.write_line(f"COHORT_NODE {node}")

        def pytest_runtest_logstart(self, nodeid):
            print(f"\nCOHORT_START {nodeid}", flush=True)

        def pytest_runtest_logfinish(self, nodeid):
            self.data["finished"].append(nodeid)
            print(f"\nCOHORT_FINISH {nodeid}", flush=True)

    plugin = Selection()
    # Replace any earlier success manifest even if pytest is interrupted.
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(plugin.data, indent=2) + "\n")
    try:
        result = int(pytest.main(pytest_args, plugins=[plugin, pytest_diagnostics]))
        if result == 0:
            selected = plugin.data["selected"]
            finished = plugin.data["finished"]
            if not selected or (
                not plugin.data["collect_only"] and Counter(finished) != Counter(selected)
            ):
                result = int(pytest.ExitCode.NO_TESTS_COLLECTED)
        plugin.data["pytest_exit_code"] = result
        return result
    finally:
        manifest.write_text(json.dumps(plugin.data, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--cohort", choices=COHORTS, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--manifest-dir", type=Path, required=True)
    for command in (run, verify):
        command.add_argument("--source-sha", required=True)
    run.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
        parser.error("source SHA must be a full lowercase Git commit ID")
    if args.command == "run":
        pytest_args = args.pytest_args
        if pytest_args[:1] == ["--"]:
            pytest_args = pytest_args[1:]
        return run_cohort(args.cohort, args.manifest, args.source_sha, pytest_args)
    try:
        verify_manifests(args.manifest_dir, args.source_sha)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(1, f"Coverage verification failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
