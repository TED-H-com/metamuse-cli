"""Independent review NM1–NM7 and round-7 control removals, opt-in only.

Adapted to the actual CDP/proxy APIs. NM3 restores the reported old behavior
rather than preserving the review harness's no-op wrapper around an already
broken snapshot. No production source or /tmp probe is imported or modified.
"""

import inspect
import os
import textwrap
from dataclasses import replace

import pytest

from metamuse import _browser_egress, facebook
from metamuse import models as m


def apply_mutant(monkeypatch, mutant):
    if mutant == "NM1":
        original = facebook.build_chromium_launch_args
        monkeypatch.setattr(
            facebook,
            "build_chromium_launch_args",
            lambda port: [arg for arg in original(port) if arg != "--block-new-web-contents"],
        )
    elif mutant == "NM2":
        monkeypatch.setattr(facebook, "_verify_popup_control", lambda *args: None)
    elif mutant == "NM3":
        snapshot = facebook._PlaywrightSession._snapshot_html

        def keep_page_marker(self):
            evaluate = self._page.evaluate

            def preserve(script):
                old = "if (attr.name.toLowerCase().startsWith('data-metamuse-'))"
                new = (
                    "if (attr.name.toLowerCase().startsWith('data-metamuse-') && "
                    "attr.name !== 'data-metamuse-invisible')"
                )
                assert old in script
                return evaluate(script.replace(old, new))

            with monkeypatch.context() as patch:
                patch.setattr(self._page, "evaluate", preserve)
                return snapshot(self)

        monkeypatch.setattr(facebook._PlaywrightSession, "_snapshot_html", keep_page_marker)
        start = facebook._DOMExtractor.handle_starttag

        def trust_page(self, tag, attrs):
            if any(key == "data-metamuse-invisible" for key, _ in attrs):
                attrs = [*attrs, ("hidden", "")]
            return start(self, tag, attrs)

        monkeypatch.setattr(facebook._DOMExtractor, "handle_starttag", trust_page)
    elif mutant == "NM4":
        # The actual build tracks pending candidates in _active_sockets too.
        # Preserve the review's original getpeername-based fault injection.
        register = _browser_egress.BrowserEgressProxy._register_socket

        def delayed_register(self, sock):
            try:
                sock.getpeername()
            except OSError:
                return True
            return register(self, sock)

        monkeypatch.setattr(
            _browser_egress.BrowserEgressProxy, "_register_socket", delayed_register
        )
    elif mutant == "NM5":
        check = facebook.EgressGuard.check_browser_route

        def classify_first(self, url, *, is_navigation=False, **kwargs):
            if is_navigation:
                barrier = self._classify_navigation_barrier(url)
                if barrier is not None:
                    self.navigation_error = barrier
                    raise barrier
                self.validate_navigation_identity(url)
            return check(self, url, is_navigation=is_navigation, **kwargs)

        monkeypatch.setattr(facebook.EgressGuard, "check_browser_route", classify_first)
    elif mutant == "NM6":

        def ignore_failed_close(self, target_id):
            try:
                self._worker_cdp.send("Target.closeTarget", {"targetId": target_id})
            except Exception:
                pass

        monkeypatch.setattr(facebook._PlaywrightSession, "_close_target", ignore_failed_close)
    elif mutant == "NM7":

        def direct_only(browser, context, page):
            refused = page.evaluate("""() => {
              const direct = window.open('');
              if (direct) direct.close();
              return direct === null;
            }""")
            if refused and len(context.pages) == 1:
                return
            raise m.CoreError(
                m.ErrorCode.DEPENDENCY_MISSING,
                "Facebook browser popup creation control is unavailable",
            )

        monkeypatch.setattr(facebook, "_verify_popup_control", direct_only)
    elif mutant == "R7F1":
        start = facebook._DOMExtractor.handle_starttag

        def no_visibility_override(self, tag, attrs):
            if self._visibility_stack and not self._visibility_stack[-1]:
                attrs = [*attrs, ("hidden", "")]
            return start(self, tag, attrs)

        monkeypatch.setattr(facebook._DOMExtractor, "handle_starttag", no_visibility_override)
    elif mutant == "R7F2":
        close = facebook._PlaywrightSession._close_target

        def immediate_refusal(self, target_id):
            close(self, target_id)
            if not self._target_gone(target_id):
                self._guard_failed()

        monkeypatch.setattr(facebook._PlaywrightSession, "_close_target", immediate_refusal)
    elif mutant == "R7F3":
        source = inspect.getsource(facebook.run_facebook_post)
        old = "if exc.detail.code == m.ErrorCode.LIMIT_REACHED:"
        assert old in source
        source = source.replace(
            old, "if exc.detail.code == m.ErrorCode.LIMIT_REACHED or egress.byte_limit_exceeded:"
        )
        namespace = {}
        exec(compile(textwrap.dedent(source), "<R7F3>", "exec"), facebook.__dict__, namespace)
        handler = namespace["run_facebook_post"]
        monkeypatch.setattr(facebook, "run_facebook_post", handler)
        if facebook.OPERATION in facebook.jobs.OPERATIONS:
            monkeypatch.setitem(
                facebook.jobs.OPERATIONS,
                facebook.OPERATION,
                replace(facebook.jobs.OPERATIONS[facebook.OPERATION], handler=handler),
            )
    else:
        raise ValueError(f"Unknown round-7 mutant: {mutant}")


@pytest.fixture(autouse=True)
def inject_round7_mutant(monkeypatch):
    mutant = os.environ.get("METAMUSE_FACEBOOK_ROUND7_MUTANT")
    if mutant:
        apply_mutant(monkeypatch, mutant)
