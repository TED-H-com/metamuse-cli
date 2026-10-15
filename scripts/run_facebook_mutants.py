"""Run historical, reviewer and round-9 B13 faults in memory, one pytest at a time."""

import argparse
import json
import subprocess
from pathlib import Path

CASES = {
    "M1": ["tests/test_facebook_round5.py", "-k", "network_blocks_page_websocket_bypasses"],
    "M2": ["tests/test_facebook_round5.py", "-k", "r5_n3"],
    "M3": ["tests/test_facebook_round5.py", "-k", "r5_n6"],
    "M4": ["tests/test_facebook_round5.py", "-k", "r5_12"],
    "M5": ["tests/test_facebook_round5.py", "-k", "r5_n4"],
    "M6": ["tests/test_facebook_review.py", "-k", "finding_04"],
    "M7": ["tests/test_facebook_guard_failures.py", "-k", "popup_close_exception"],
    "M8": ["tests/test_facebook_xhr.py"],
    "M9": ["tests/test_facebook_round6.py", "-k", "barrier_markers"],
    "F1": ["tests/test_facebook_popups.py"],
    "F2": ["tests/test_facebook_round6.py", "-k", "barrier_markers"],
    "F3": ["tests/test_facebook_round6.py", "-k", "policy_precedes"],
    "GUARD": [
        "tests/test_facebook_guard_failures.py",
        "-k",
        "worker_shutdown or disconnected_browser",
    ],
    "G1": ["tests/test_facebook_byte_stop.py", "-k", "closes_registered_sockets"],
}

CASES.update(
    {
        "NM1": ["tests/test_facebook_popups.py", "-k", "popup_creation_refused"],
        "NM2": ["tests/test_facebook_popups.py", "-k", "missing_popup_control"],
        "NM3": ["tests/test_facebook_visibility.py", "-k", "page_cannot_hide"],
        "NM4": ["tests/test_facebook_byte_stop.py", "-k", "connecting_upstream"],
        "NM5": ["tests/test_facebook_round6.py", "-k", "policy_precedes or denied_navigation"],
        "NM6": ["tests/test_facebook_guard_failures.py", "-k", "worker_shutdown"],
        "NM7": ["tests/test_facebook_popups.py", "-k", "iframe_only"],
        "R7F1": ["tests/test_facebook_visibility.py", "-k", "visible-child"],
        "R7F2": ["tests/test_facebook_target_shutdown.py", "-k", "false_close_waits"],
        "R7F3": ["tests/test_facebook_cutoff_precedence.py", "-k", "fatal_stop_identical"],
    }
)

ROUND8_CASES = {
    "RM1": ["tests/test_facebook_visibility.py", "-k", "raw_html_ignores"],
    "RM2": ["tests/test_facebook_r8_capture.py", "-k", "svg-marker"],
    "RM3": ["tests/test_facebook_visibility.py", "-k", "visible-child"],
    "RM4": ["tests/test_facebook_target_shutdown.py", "-k", "false_close_waits"],
    "RM5": ["tests/test_facebook_cutoff_precedence.py", "-k", "login_observation"],
    "RM6": [
        "tests/test_facebook_cutoff_precedence.py",
        "-k",
        "guard_verified_after_cutoff and close",
    ],
    "RM7": [
        "tests/test_facebook_r8_capture.py",
        "tests/test_facebook_snapshot_case.py",
        "-k",
        "(cannot_be_forged and namespace) or case_preserving",
    ],
    "RM8": ["tests/test_facebook_cutoff_observation.py"],
    "RM9": ["tests/test_facebook_cutoff_precedence.py", "-k", "simultaneous_fatal_precedence"],
    "RM10": ["tests/test_facebook_round6.py", "-k", "hidden_exhaustion"],
    "R8_MAIN": ["tests/test_facebook_r8_capture.py", "-k", "cannot_be_forged and walker"],
    "R8_MUTATE": ["tests/test_facebook_r8_capture.py", "-k", "capture_is_read_only"],
    "R8_AUTO": ["tests/test_facebook_r8_capture.py", "-k", "table_parity and auto"],
    "R8_CONTENTS": ["tests/test_facebook_r8_capture.py", "-k", "table_parity and contents"],
    "R8_CLOSED": ["tests/test_facebook_r8_capture.py", "-k", "shadow_rendering and closed"],
    "R8_NOSCRIPT": ["tests/test_facebook_r8_capture.py", "-k", "noscript_cannot and fixture"],
    "R8_VOID": ["tests/test_facebook_r8_capture.py", "-k", "void-hidden"],
    "R8_COPY": ["tests/test_facebook_r8_workers.py", "-k", "reentrant_attach"],
    "R8_SHARE": [
        "tests/test_facebook_r8_cutoff.py",
        "-k",
        "fatal_cutoff and cutoff-first and (E_UNSUPPORTED or E_DATA_STALE)",
    ],
    "R8_DNS": ["tests/test_facebook_r8_cutoff.py", "-k", "dns_free_policy_before_latch"],
    "R8_OVERWRITE": ["tests/test_facebook_r8_cutoff.py", "-k", "recorded_fatal"],
}
CASES.update(ROUND8_CASES)

REVIEWER_CASES = {
    "REV_M1_MAIN_PROP": ["tests/test_facebook_r8_capture.py", "-k", "cannot_be_forged and walker"],
    "REV_M1B_MAIN_CONTENT_TYPE": [
        "tests/test_facebook_r9_capture.py",
        "-k",
        "reviewer_clobbering_rows and Object",
    ],
    "REV_M2_CLOSED_ROOT": [
        "tests/test_facebook_r8_capture.py",
        "-k",
        "shadow_rendering and closed",
    ],
    "REV_M2B_SECOND_CLOSED_ROOT": ["tests/test_facebook_r9_survivors.py", "-k", "two_closed_roots"],
    "REV_M3_CV_HIDDEN": ["tests/test_facebook_r8_capture.py", "-k", "table_parity and cv-hidden"],
    "REV_M4_CONTENTS_LOOKTHROUGH": [
        "tests/test_facebook_r8_capture.py",
        "-k",
        "table_parity and contents",
    ],
    "REV_M5_BARRIER_SKIP": [
        "tests/test_facebook_r8_capture.py",
        "-k",
        "barriers_in_every_tree and template",
    ],
    "REV_M6_NODE_BOUND_OFF_BY_ONE": [
        "tests/test_facebook_r9_survivors.py",
        "-k",
        "exact_node_bounds",
    ],
    "REV_M7_CAPTURE_ERR_CODE": [
        "tests/test_facebook_r9_capture.py",
        "-k",
        "fixture_capture_preserves",
    ],
    "REV_M8_CLICK_HIT_TEST": [
        "tests/test_facebook_r9_capture.py",
        "-k",
        "click_identity and overlay",
    ],
    "REV_M9_DEADLINE_RESET": ["tests/test_facebook_r9_survivors.py", "-k", "single_deadline"],
    "REV_M10_CUTOFF_FILTER": [
        "tests/test_facebook_r8_cutoff.py",
        "-k",
        "fatal_cutoff and cutoff-first and E_REFERENCE_INVALID",
    ],
    "REV_M10B_CUTOFF_SELECTOR_DRIFT": [
        "tests/test_facebook_r9_survivors.py",
        "-k",
        "every_core and E_SELECTOR_DRIFT",
    ],
}
ROUND9_CASES = {
    "R9_ACCEPT_TEMPLATE_WRAPPER": [
        "tests/test_facebook_r9_capture.py",
        "-k",
        "document_wrappers_in_template",
    ],
    "R9_MARKER_VOCABULARY": ["tests/test_facebook_r9_parity.py"],
    **{
        name: [
            "tests/test_facebook_r9_capture.py",
            "-k",
            "native_void_rendering or unmodelled_native_rendering",
        ]
        for name in (
            "R9_NATIVE_VOID",
            "R9_SVG_OPAQUE",
            "R9_SVG_CHILDREN",
            "R9_SVG_TSPAN",
            "R9_FOREIGN_DISPLAY",
            "R9_COLUMN_DISPLAY",
        )
    },
    **{
        f"R9_DIRECT_{group}": ["tests/test_facebook_r9_capture.py", "-k", "no_direct"]
        for group in (
            "DOCUMENT",
            "NODE",
            "ELEMENT",
            "ATTRIBUTE",
            "TEMPLATE",
            "SLOT",
            "RANGE",
            "STYLE",
            "COLLECTION",
            "SHADOW",
            "IFRAME",
            "TEXT",
            "WINDOW",
            "ATTRS_COLLECTION",
            "RECTS_COLLECTION",
        )
    },
    "R9_NAMESPACE": ["tests/test_facebook_r9_capture.py", "-k", "foreign_template_slot"],
    "R9_REWRAP": ["tests/test_facebook_r9_capture.py", "-k", "fixture_capture_preserves"],
    **{
        f"R9_ACCEPT_{construct}": [
            "tests/test_facebook_r9_capture.py",
            "-k",
            "fixture_refuses_unsupported",
        ]
        for construct in (
            "XMP",
            "IFRAME",
            "NOEMBED",
            "NOFRAMES",
            "PLAINTEXT",
            "TEXTAREA",
            "TITLE",
            "DIALOG",
            "POPOVER",
            "FOREIGN",
            "UNBALANCED",
            "DSD",
            "P",
            "TABLE",
            "SCRIPT",
            "STYLE",
            "SELECT",
            "OPTION",
            "OPTGROUP",
            "FRAMESET",
            "FRAME",
            "EMBED",
        )
    },
    "R9_COMPOSED_PARENT": ["tests/test_facebook_r9_capture.py", "-k", "click_identity and slotted"],
    "R9_ACCEPT_METADATA": ["tests/test_facebook_r9_capture.py", "-k", "metadata_display_override"],
    "R9_ACCEPT_GLOBAL_DISPLAY": [
        "tests/test_facebook_r9_capture.py",
        "-k",
        "unresolved_display_globals",
    ],
    "R9_ACCEPT_WHITESPACE": ["tests/test_facebook_r9_capture.py", "-k", "unmodelled_layout"],
    "R9_ACCEPT_INTEGRATION": ["tests/test_facebook_r9_capture.py", "-k", "unmodelled_layout"],
    **{
        f"R9_ACCEPT_{kind}": ["tests/test_facebook_r9_capture.py", "-k", "lexical_differentials"]
        for kind in ("ENTITIES", "NULL", "CDATA", "PI")
    },
    "R9_IMG_LOOKTHROUGH": [
        "tests/test_facebook_r9_capture.py",
        "-k",
        "unrendered_container_state and img",
    ],
    "R9_HIDDEN_STATE": [
        "tests/test_facebook_r9_capture.py",
        "-k",
        "unrendered_container_state and post",
    ],
    "R9_CONTROL_KEY": ["tests/test_facebook_r9_capture.py", "-k", "click_identity and missing"],
    "R9_DUPLICATE_LAST": [
        "tests/test_facebook_r9_capture.py",
        "-k",
        "click_identity and duplicate",
    ],
    "R9_CONTAINS": [
        "tests/test_facebook_r9_capture.py",
        "-k",
        "click_identity and (own-shadow or slotted)",
    ],
    "R9_REVALIDATE": [
        "tests/test_facebook_r9_capture.py",
        "-k",
        "click_identity and hidden-before-capture",
    ],
}
CASES.update(REVIEWER_CASES)
CASES.update(ROUND9_CASES)

OPTIONAL_CASES = {
    "G1_WATCH": [
        "tests/test_facebook.py::test_playwright_egress_proxy_byte_budget_stops_oversized_response"
    ],
}
ALL_CASES = {**CASES, **OPTIONAL_CASES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Disk log directory outside the repository")
    parser.add_argument("--only", choices=list(ALL_CASES), nargs="+")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output / "results.json"
    results = json.loads(result_path.read_text()) if result_path.exists() else {}
    for name in args.only or CASES:
        command = [
            "pytest-disktmp",
            "env",
            "UV_OFFLINE=1",
            "METAMUSE_REQUIRE_BROWSER_TESTS=1",
            "PYTHONPATH=tests",
            "PYTEST_PLUGINS=facebook_mutants,facebook_round7_mutants,facebook_round8_mutants,facebook_reviewer8_mutants,facebook_round9_mutants",
            (
                f"METAMUSE_FACEBOOK_ROUND9_MUTANT={name}"
                if name in ROUND9_CASES
                else f"METAMUSE_REVIEWER_R8_MUTANT={name}"
                if name in REVIEWER_CASES
                else f"METAMUSE_FACEBOOK_ROUND8_MUTANT={name}"
                if name in ROUND8_CASES
                else f"METAMUSE_FACEBOOK_ROUND7_MUTANT={name}"
                if name.startswith(("NM", "R7"))
                else f"METAMUSE_FACEBOOK_MUTANT={name}"
            ),
            "uv",
            "run",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *ALL_CASES[name],
        ]
        with (args.output / f"{name}.log").open("w") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        output = (args.output / f"{name}.log").read_text()
        failures = [line for line in output.splitlines() if line.startswith("FAILED ")]
        killed = result.returncode == 1 and bool(failures)
        results[name] = {
            "result": "KILLED" if killed else "NOT_KILLED",
            "returncode": result.returncode,
            "failures": failures,
            "command": command,
        }
        print(f"{name}: {results[name]['result']} ({len(failures)} killing tests)", flush=True)
        (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    return int(any(row["result"] != "KILLED" for row in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
