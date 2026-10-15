"""The surviving reviewer faults, with direct assertions and shared observations."""

# ruff: noqa: E501

from browser_deadline import browser_deadline
from facebook_parity import capture_session, captured_parse
from test_facebook import _synthetic_post_html
from test_facebook_round5 import parse
from test_facebook_round6 import reset_seams  # noqa: F401


@browser_deadline
def test_r10_foreign_noscript_iframe_and_contents(tmp_path, monkeypatch):
    with capture_session(monkeypatch) as session:
        for foreign in ("svg", "math"):
            for tag in ("noscript", "iframe"):
                markup = _synthetic_post_html(post_text="BODY").replace(
                    ">BODY</div>",
                    ">Lead<"
                    + foreign
                    + "><"
                    + tag
                    + "><"
                    + ("text" if foreign == "svg" else "mtext")
                    + ">FOREIGN_PAINTED</"
                    + ("text" if foreign == "svg" else "mtext")
                    + "></"
                    + tag
                    + "></"
                    + foreign
                    + "></div>",
                )
                actual = captured_parse(session, markup)
                assert ("FOREIGN_PAINTED" in actual.text) == (foreign == "math"), (
                    foreign,
                    tag,
                    actual.text,
                )
        for tag in ("button", "fieldset", "legend"):
            fragment = "<" + tag + ' style="display:contents"><span>PAINTED</span></' + tag + ">"
            if tag == "legend":
                fragment = "<fieldset>" + fragment + "</fieldset>"
            markup = _synthetic_post_html(post_text="BODY").replace(
                ">BODY</div>", ">" + fragment + "</div>"
            )
            assert "PAINTED" in parse(markup).text
            assert "PAINTED" in captured_parse(session, markup).text
