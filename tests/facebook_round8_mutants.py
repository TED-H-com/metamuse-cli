"""Opt-in historical and round-8 faults; never loaded in ordinary test runs."""

import inspect
import os
import textwrap
from dataclasses import replace

import pytest

from metamuse import _browser_egress, facebook
from metamuse import _facebook_capture as capture
from metamuse import models as m


def rewrite(monkeypatch, owner, name, edits, module=facebook):
    source = textwrap.dedent(inspect.getsource(getattr(owner, name)))
    for old, new in edits:
        assert old in source, (name, old)
        source = source.replace(old, new)
    namespace = {}
    exec(compile(source, "<r8-mutant-" + name + ">", "exec"), module.__dict__, namespace)
    function = namespace[name]
    monkeypatch.setattr(owner, name, function)
    if name == "run_facebook_post" and facebook.OPERATION in facebook.jobs.OPERATIONS:
        monkeypatch.setitem(
            facebook.jobs.OPERATIONS,
            facebook.OPERATION,
            replace(facebook.jobs.OPERATIONS[facebook.OPERATION], handler=function),
        )


def trust_markers(monkeypatch, keys=None, namespace=None, raw_only=False):
    walk = facebook._DOMExtractor.walk

    def trusting(self, nodes):
        for node in nodes:
            attrs = node["attrs"]
            if namespace is not None and node["ns"] != namespace:
                continue
            names = keys or {
                "data-metamuse-invisible",
                "data-metamuse-display-none",
                "data-metamuse-visibility",
            }
            if any(
                k in attrs and (k != "data-metamuse-visibility" or attrs[k] != "visible")
                for k in names
            ):
                pending = [node]
                while pending:
                    entry = pending.pop()
                    entry["rendered"] = False
                    pending.extend(nodes[i] for i in entry["children"])
        return walk(self, nodes)

    if raw_only:
        fixture = capture.fixture_capture

        def trusted(markup):
            nodes = fixture(markup)
            # Apply the same defect before the ordinary extractor traverses it.
            for node in nodes:
                if any(k.startswith("data-metamuse-") for k in node["attrs"]):
                    pending = [node]
                    while pending:
                        entry = pending.pop()
                        entry["rendered"] = False
                        pending.extend(nodes[i] for i in entry["children"])
            return nodes

        monkeypatch.setattr(capture, "fixture_capture", trusted)
    else:
        monkeypatch.setattr(facebook._DOMExtractor, "walk", trusting)


def apply(monkeypatch, name):
    if name == "RM1":
        trust_markers(monkeypatch, raw_only=True)
    elif name == "RM2":
        trust_markers(monkeypatch, namespace=capture.SVG)
    elif name == "RM3":
        from facebook_round7_mutants import apply_mutant

        apply_mutant(monkeypatch, "R7F1")
    elif name == "RM4":
        rewrite(
            monkeypatch,
            facebook._PlaywrightSession,
            "_verify_target_shutdown",
            [("deadline = time.monotonic() + 5", "deadline = time.monotonic()")],
        )
    elif name == "RM5":
        edits = []
        for indent in ("        ", "            "):
            old = "\n" + indent + "parsed = classify_and_parse_observation("
            new = (
                "\n"
                + indent
                + "if egress.byte_limit_exceeded or bytes_used > max_bytes:\n"
                + indent
                + (
                    "    raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "
                    '"Cutoff before classification")\n'
                )
                + indent
                + "parsed = classify_and_parse_observation("
            )
            edits.append((old, new))
        rewrite(monkeypatch, facebook, "run_facebook_post", edits)
    elif name == "RM6":
        close = facebook._PlaywrightSession.close

        def unchecked(self):
            self._verify_target_shutdown = lambda: None
            return close(self)

        monkeypatch.setattr(facebook._PlaywrightSession, "close", unchecked)
    elif name == "RM7":
        injected = """let v_blocked=v_excluded || (v_el && (()=>{
        const n_attrs=D.attributes(n_node);
        for(let v_i=0;v_i<D.attrsLength(n_attrs);v_i++)
          if(D.attrName(D.attrsItem(n_attrs,v_i))==='DATA-METAMUSE-DISPLAY-NONE')return true;
        return false;})());"""
        assert "let v_blocked=v_excluded;" in capture.WALK
        monkeypatch.setattr(
            capture, "WALK", capture.WALK.replace("let v_blocked=v_excluded;", injected)
        )
    elif name == "RM8":

        def unpinned(self, url, purpose):
            self.egress.check_url_without_dns(url, purpose)
            if purpose in ("request", "redirect"):
                barrier = self.egress._classify_navigation_barrier(url)
                if barrier is not None:
                    raise barrier
            return self._check_bounded(lambda: self.egress._check_route_destination(url, purpose))

        monkeypatch.setattr(_browser_egress.BrowserEgressProxy, "check_url_bounded", unpinned)
    elif name == "RM9":
        priority = facebook._stop_priority
        monkeypatch.setattr(
            facebook,
            "_stop_priority",
            lambda error: 5 if error.detail.code == m.ErrorCode.SCOPE_DENIED else priority(error),
        )
        rewrite(
            monkeypatch,
            facebook._PlaywrightSession,
            "_raise_if_fatal_stop",
            [("error.detail.code != m.ErrorCode.LIMIT_REACHED", "_stop_priority(error) < 4")],
        )
    elif name == "RM10":
        walk = facebook._DOMExtractor.walk

        def completeness(self, nodes):
            result = walk(self, nodes)
            if any(n["attrs"].get("data-comments-exhausted") == "true" for n in nodes):
                self.comments_exhausted = True
            return result

        monkeypatch.setattr(facebook._DOMExtractor, "walk", completeness)
    elif name == "R8_MAIN":
        original = capture.capture

        def main(cdp):
            send = cdp.send

            def wrong_world(method, params=None):
                if method == "Runtime.evaluate":
                    params = {k: v for k, v in params.items() if k != "contextId"}
                return send(method, params)

            with monkeypatch.context() as patch:
                patch.setattr(cdp, "send", wrong_world)
                return original(cdp)

        monkeypatch.setattr(capture, "capture", main)
    elif name == "R8_MUTATE":
        monkeypatch.setattr(
            capture,
            "WALK",
            capture.WALK.replace(
                "const v_nodes=[],",
                (
                    "document.querySelector('x-count')?."
                    "setAttribute('data-capture-test','1');const v_nodes=[],"
                ),
            ),
        )
    elif name == "R8_AUTO":
        monkeypatch.setattr(
            capture,
            "WALK",
            capture.WALK.replace(
                "return D.checkVisibility(n_node);",
                "return D.checkVisibility(n_node,{contentVisibilityAuto:true});",
            ),
        )
    elif name == "R8_CONTENTS":
        monkeypatch.setattr(
            capture,
            "WALK",
            capture.WALK.replace("if(style(n_node,'display')!=='contents')", "if(true)"),
        )
    elif name == "R8_CLOSED":
        rewrite(
            monkeypatch,
            capture,
            "capture",
            [('if root.get("shadowRootType") != "closed":', "if True:")],
            module=capture,
        )
    elif name == "R8_NOSCRIPT":
        monkeypatch.setattr(capture.FixtureTree, "CDATA_CONTENT_ELEMENTS", ("script", "style"))
        rewrite(
            monkeypatch,
            capture.FixtureTree,
            "finish",
            [('"noscript",', ""), ('{"template", "noscript"}', '{"template"}')],
            module=capture,
        )
    elif name == "R8_VOID":
        monkeypatch.setattr(capture, "VOID", capture.VOID - {"bgsound"})
    elif name == "R8_COPY":
        rewrite(
            monkeypatch,
            facebook._PlaywrightSession,
            "_verify_target_shutdown",
            [
                (
                    "for target in list(self._closing_targets):",
                    "for target in self._closing_targets:",
                )
            ],
        )
    elif name == "R8_SHARE":
        rewrite(
            monkeypatch,
            facebook._PlaywrightSession,
            "_raise_if_fatal_stop",
            [
                (
                    "error.detail.code != m.ErrorCode.LIMIT_REACHED",
                    (
                        "error.detail.code != m.ErrorCode.LIMIT_REACHED and not "
                        "(self._egress.byte_limit_exceeded and error.detail.code in "
                        "{m.ErrorCode.UNSUPPORTED, m.ErrorCode.DATA_STALE})"
                    ),
                )
            ],
        )
    elif name == "R8_DNS":
        check = _browser_egress.BrowserEgressProxy.check_url_bounded

        def latch_first(self, url, purpose):
            self._raise_if_byte_limit()
            return check(self, url, purpose)

        monkeypatch.setattr(_browser_egress.BrowserEgressProxy, "check_url_bounded", latch_first)
    elif name == "R8_OVERWRITE":
        monkeypatch.setattr(facebook, "_keep_stop", lambda previous, incoming: incoming)
    else:
        raise ValueError(name)


@pytest.fixture(autouse=True)
def inject_round8_mutant(monkeypatch):
    name = os.environ.get("METAMUSE_FACEBOOK_ROUND8_MUTANT")
    if name:
        apply(monkeypatch, name)
