"""One adversarial family for each scanner's repeated-search risk."""

from b16a_r6_complexity import build as prior_build
from b16a_r6_complexity import complexity_criterion as complexity_criterion
from b16a_r6_complexity import families as prior_families

NEW = {
    "xml_unclosed_before_exempt": ("<password ", ">None"),
    "html_quoted_subtags": ('a="<b " ', ">"),
    "folded_header": ('  Authorization: ""\n', ""),
    "ownership_quotes": ("users' password=pa'-a ", ""),
    "query_closer_runs": ("?token=pa]-a ", ""),
    "userinfo_closer_runs": ("https://a:pa]-a@host/p ", ""),
    "curl_exempt_reentry": ('curl -b "${C:- -u alice:a}" ', ""),
    "command_exempt_reentry": ('sshpass -p "${P:-sshpass -p a}" ', ""),
    "shell_defaults": ("password=${PASSWORD:-${FALLBACK}} ", ""),
    "markup_comment": ("<!-- <input name=password value=a/> --> ", ""),
    "yaml_indicators": ("password: |2- # note\n  a\n", ""),
    "putty_final_missing": ("Pu" + "TTY-User-Key-File-3: a\nComment: ] a\n", ""),
    "form_markup": ('Log: body=<input+name="password"+value="a"> ', ""),
    "har_reentry": ('{"name":"sid","value":"${S:-password=a}"} ', ""),
    "trailing_closers": ("password=a", "\"'`)]}>.,!?:;"),
    "attribute_closer_run": ('<input name=password value="', '"'),
    "sql_anchor_missing": ("CREATE ", ""),
    "line_depths": ("Authorization: token\\n ", ""),
    "markup_nested_private": ('<input name=password value="', '"'),
    "doubled_quotes": ('password="a"" a" ', ""),
}


def families():
    return prior_families() + tuple("r7_" + name for name in NEW)


def build(family, size):
    if not family.startswith("r7_"):
        return prior_build(family, size)
    name = family[3:]
    unit, suffix = NEW[name]
    prefix = (
        "<div "
        if name == "html_quoted_subtags"
        else 'Authorization: ""\n'
        if name == "folded_header"
        else ""
    )
    if name == "markup_nested_private":
        count = max(1, size // (len(unit) + 2))
        return unit * count + "v" + " " * (2 * count) + suffix
    if name == "attribute_closer_run":
        return unit + suffix * max(1, size // len(suffix)) + ">"
    if name == "trailing_closers":
        return unit + suffix * max(1, size // len(suffix))
    return prefix + unit * max(1, size // len(unit)) + suffix
