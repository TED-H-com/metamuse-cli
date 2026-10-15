"""Orchestrator inputs retained independently of the reviewer corpus."""

V = f"{'ORCH7'}_MARKER_PRIVATE_654329"

ROWS = []

cases = {
    "R6-1": [
        f"[users' 'https://alice:pa]-{V}@example.invalid/p']",
        f'''See the users' "https://alice:pa'-{V}@example.invalid/p"''',
        f"users' password=pa'-{V}",
        f"users' sid=pa'-{V}; theme=dark",
    ],
    "R6-2": [
        f"password=<{V}%3e",
        f"https://alice:${{{V}%7d@example.invalid/p",
        "cu" + f'rl -b "${{C:--u alice:{V}}}"',
        f'<input name="password" value="<{V}%3e">',
    ],
    "R6-3": [
        f'<input value="{V}" name=password/>',
        f'<input type=" password " value="{V}">',
        f"<!-- <password user's tag> -->\n<password>{V}</password>",
    ],
}

ROWS.extend(
    ("high:" + family + ":" + str(i), text)
    for family, values in cases.items()
    for i, text in enumerate(values)
)

rows = [
    f"password=${{PASSWORD:-{V}}}",
    'export API_KEY="${API_KEY:-${FALLBACK_KEY}}"',
    "token=${TOKEN:-}",
    f'x = "user=bob password={V}" + y',
    'x = "user=%s password=%s" % (u, p)',
    "x = 'password={}'.format(pw)",
    'x = "token=%s".format(tok)',
    '["https://host:8080","alice@example.invalid"]',
    "['https://host:8080','alice@example.invalid']",
    f"{{'password': {V}}}",
    f"{{'user': 'bob', 'password': '{V}'}}",
    f"password={V},user=bob",
    f"f(password={V}, user=bob)",
    "cu" + "rl " + f"-u 'alice:{V}' https://example.invalid",
    '"cu' + "rl " + f'''-u 'alice:{V}'"''',
    'cmd = "cu' + "rl " + f'''-u 'alice:{V}' https://x"''',
    f"(see https://example.invalid/p?token={V})",
    f"[link](https://alice:{V}@example.invalid/p)",
    f"'https://alice:pa',{V}@example.invalid/p'",
    f"""password='pa'"'"'{V}'""",
    "url = 'https://example.invalid/p?token=%s' % tok",
    "msg = 'password=%s' % pw",
]

ROWS.extend(("semantics:" + str(i), text) for i, text in enumerate(rows))

rows = [
    f"(note password=pa,{V})",
    f"(users' password=pa,{V})",
    f"print('x', password=pa,{V})",
    f"Set password=pa,{V} now",
    f"the users' sid=pa,{V}",
    f"Cookie: sid=pa,{V}",
    "Set password=${PW}.",
    "Use `password=${PW}`.",
    "Use `token=<your token>`.",
    f"Use `password={V}`.",
    "x = 'password=%s'%pw",
    "x = f('password=%s',x)",
    '<input value="abc"/>',
    f'<input name="password" value="{V}"/>',
    f'"password=pa"" {V}"',
    "cu" + "rl " + f"-u 'alice:pa'\\''{V}' https://example.invalid",
    f"password = “{V} {V}”",
    f'{{"password":"pa\\",{V}"}}',
]

ROWS.extend(("separators:" + str(i), text) for i, text in enumerate(rows))
