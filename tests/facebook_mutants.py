"""Opt-in review mutants, applied in memory; no mutation changes tracked sources.

M1–M9 preserve the independent round-5 review's fault injection, adapted to the
current APIs. R6 mutations remove one required control each. Not loaded normally.
"""

import asyncio
import os
import re
from dataclasses import replace

import pytest

from metamuse import _browser_egress, facebook


def apply_mutant(monkeypatch, mutant):
    if mutant in {"M1", "M2", "M6", "M7", "M8"}:
        original = facebook._PlaywrightSession.__init__

        def initialize(self, pw, browser, context, page, *args, **kwargs):
            if mutant == "M2":
                register = context.route

                def wrapped_route(pattern, handler):
                    def route(actual):
                        request = actual.request
                        if request.is_navigation_request() and request.frame != page.main_frame:
                            self._egress.check_browser_route(request.url, is_navigation=True)
                        return handler(actual)

                    return register(pattern, route)

                monkeypatch.setattr(context, "route", wrapped_route)
            if mutant == "M7":
                context_on, page_on = context.on, page.on
                monkeypatch.setattr(
                    context,
                    "on",
                    lambda event, cb: None if event == "page" else context_on(event, cb),
                )
                monkeypatch.setattr(
                    page, "on", lambda event, cb: None if event == "popup" else page_on(event, cb)
                )
            if mutant == "M8":
                add_script = context.add_init_script

                def script(value):
                    return add_script(
                        re.sub(
                            r"const urls = new WeakMap\(\).*?"
                            r"XMLHttpRequest\.prototype\.send = function.*?};",
                            "",
                            value,
                            flags=re.DOTALL,
                        )
                    )

                monkeypatch.setattr(context, "add_init_script", script)
            original(self, pw, browser, context, page, *args, **kwargs)
            if mutant == "M1":
                self._network_cdp.send("Network.setBlockedURLs", {"urls": []})
                self._network_cdp.send(
                    "Network.emulateNetworkConditionsByRule", {"matchedNetworkConditions": []}
                )
            if mutant == "M6":
                # Original review injection: fail the per-worker CDP channel.
                # The redundant network rules are gone; fixed paused shutdown
                # is now load-bearing at this same channel boundary.
                def missing_channel():
                    raise RuntimeError("mutant removed worker CDP controls")

                monkeypatch.setattr(browser, "new_browser_cdp_session", missing_channel)

        monkeypatch.setattr(facebook._PlaywrightSession, "__init__", initialize)
    elif mutant == "M3":

        def integer(value):
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value if value >= 0 else None
            if isinstance(value, str) and value.strip().isdigit():
                try:
                    return int(value.strip())
                except ValueError:
                    return 0
            return None

        monkeypatch.setattr(facebook, "_opt_int", integer)
    elif mutant == "M4":
        parse = facebook._parse_json_payload

        def parsed(*args, **kwargs):
            page = parse(*args, **kwargs)
            return replace(
                page,
                comments=[
                    replace(comment, parent_native_id=comment.parent_native_id or page.post_id)
                    for comment in page.comments
                ],
            )

        monkeypatch.setattr(facebook, "_parse_json_payload", parsed)
    elif mutant == "M5":
        monkeypatch.setattr(facebook, "_observed_sandbox_mode", lambda browser: "enabled")
    elif mutant == "M9":
        snapshot = facebook._PlaywrightSession._snapshot_html

        def html(self):
            value = snapshot(self)
            for token in (
                "data-fb-state=",
                "data-page-state=",
                "data-checkpoint=",
                'id="login_form"',
                'data-testid="royal_login_form"',
                'id="checkpoint_Form"',
            ):
                value = value.replace(token, "data-ignored=")
            return value

        monkeypatch.setattr(facebook._PlaywrightSession, "_snapshot_html", html)
    elif mutant == "F1":
        arguments = facebook.build_chromium_launch_args
        monkeypatch.setattr(
            facebook,
            "build_chromium_launch_args",
            lambda port: [arg for arg in arguments(port) if arg != "--block-new-web-contents"],
        )
    elif mutant == "F2":
        start = facebook._DOMExtractor.handle_starttag

        def visible_only(self, tag, attrs):
            if self._hidden_depth or any(key == "hidden" for key, _ in attrs):
                attrs = [
                    (key, value)
                    for key, value in attrs
                    if key
                    not in (
                        "data-fb-state",
                        "data-page-state",
                        "data-checkpoint",
                        "id",
                        "data-testid",
                    )
                ]
            return start(self, tag, attrs)

        monkeypatch.setattr(facebook._DOMExtractor, "handle_starttag", visible_only)
    elif mutant == "F3":
        initialize = facebook.EgressGuard.__init__

        def policy(self, *args, **kwargs):
            initialize(self, *args, **kwargs)
            self._route_url_checker = lambda url, purpose: None

        monkeypatch.setattr(facebook.EgressGuard, "__init__", policy)
    elif mutant == "GUARD":
        monkeypatch.setattr(facebook._PlaywrightSession, "_guard_failed", lambda self: None)
    elif mutant == "G1":
        charge = _browser_egress.BrowserEgressProxy._charge_bytes

        def lazy_cutoff(self, count):
            close_socket = self._close_socket
            self._close_socket = lambda sock: None
            try:
                return charge(self, count)
            finally:
                self._close_socket = close_socket

        monkeypatch.setattr(_browser_egress.BrowserEgressProxy, "_charge_bytes", lazy_cutoff)
    elif mutant == "G1_WATCH":
        # Optional redundancy diagnostic retained from the initial matrix.
        async def no_interrupt(self):
            while True:
                await asyncio.sleep(0.05)

        monkeypatch.setattr(facebook._PlaywrightSession, "_watch_byte_limit", no_interrupt)
    else:
        raise ValueError("Unknown Facebook mutant")


@pytest.fixture(autouse=True)
def inject_mutant(monkeypatch):
    mutant = os.environ.get("METAMUSE_FACEBOOK_MUTANT")
    if mutant:
        apply_mutant(monkeypatch, mutant)
