"""Incremental curl/SQL families; inherited scanners retain their own evidence."""

import json

from b16a_r7_complexity import build as prior_build
from b16a_r7_complexity import complexity_criterion as complexity_criterion
from b16a_r7_complexity import families as prior_families

NEW = {
    "r8_owned_separators": 'curl -H "X:A;B|C&D\nE" x\\;y -b a; git -b public\n',
    "r8_exempt_argument": 'curl -b os.getenv("public;message") -b a; git -b public\n',
    "r8_sql_order": "CREATE IDENTIFIED BY 'public' USER a; ALTER USER a WITH PASSWORD 'a'; ",
    "r8_json_escape_parity": json.dumps({"cmd": "curl x" + chr(92) * 3 + ";y -b a"}) + "\n",
}


def families():
    affected = tuple(
        f
        for f in prior_families()
        if any(word in f for word in ("curl", "sql", "mysql", "sshpass", "quoted_command"))
    )
    return (
        affected
        + (
            "r7_xml_unclosed_before_exempt",
            "r7_html_quoted_subtags",
            "r7_folded_header",
            "path_unclosed_alt_quotes",
        )
        + tuple(NEW)
        + ("r8_escape_run", "r8_quote_run")
    )


def build(family, size):
    if family == "r8_escape_run":
        return "curl x" + chr(92) * size + "; -b public"
    if family == "r8_quote_run":
        return 'curl -H "' + "'\"`)]}>" * (size // 7) + '" -b a'
    if family in NEW:
        unit = NEW[family]
        return (unit * (size // len(unit) + 1))[:size]
    return prior_build(family, size)
