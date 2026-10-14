"""OFFLINE: login-form classification follows HTML first-attribute/first-base rules."""

import pytest

from metamuse import remote_media


@pytest.mark.parametrize(
    "html,document",
    [
        ('<form action="/login/" action="/safe/"></form>', "/watch/"),
        ('<base href="/login/" href="/safe/"><form action="child"></form>', "/watch/"),
        ('<base href="http://[::1"><base href="/safe/"><form action="child"></form>', "/login/"),
        ('<base href><base href="/safe/"><form action="child"></form>', "/login/"),
        ('<form action="http://[::1"></form><form action="/login/"></form>', "/watch/"),
        ('<base href="http://[::1"><base href="/safe/"><form action="/login/"></form>', "/watch/"),
        ('<base href="/login/"><base href="/safe/"><form action="child"></form>', "/watch/"),
    ],
)
def test_r9_login_form_html_first_attribute_and_base(html, document):
    assert remote_media._login_form(html.encode(), "https://www.facebook.com" + document)


def test_r9_invalid_first_base_does_not_use_a_later_login_base():
    assert not remote_media._login_form(
        b'<base href="http://[::1"><base href="/login/"><form action="child">',
        "https://www.facebook.com/watch/",
    )
