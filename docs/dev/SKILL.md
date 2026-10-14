# Skill installation

The canonical Skill tree is `skills/metamuse/`. The wheel ships those same files at
`metamuse/data/skills/metamuse/`. `metamuse skill export` writes that packaged copy
into a directory the operator names. It is not an MCP tool. It does not create or
edit an agent or host configuration directory unless that path is the `--dest` the
operator passed. A second export refuses to replace existing files until `--force`
is given.

From a checkout, after `uv sync --python 3.12`:

```bash
DEST=$(mktemp -d "${TMPDIR:-/tmp}/metamuse-skill-XXXXXX")
uv run metamuse skill export --dest "$DEST"
```

`$DEST/SKILL.md` and `$DEST/references/` are the Skill. Point the agent at `$DEST`
yourself. The Skill does not run tools: if the MetaMuse CLI or MCP server is not
installed and reachable, say so and stop (AT-027).

`tests/test_skill_export.py` exports to a temporary directory and checks those bytes
against `skills/metamuse/`. The wheel smoke script checks the same files inside the
built wheel.
