#!/usr/bin/env bash
# Required installed-wheel acceptance; run only on an authorized final candidate.
set -euo pipefail
unset LD_LIBRARY_PATH PYTHONPATH PYTHONNOUSERSITE PYTEST_PLUGINS PYTEST_ADDOPTS
export PATH="$HOME/.local/bin:$PATH"
export UV_MANAGED_PYTHON=true PYTHONDONTWRITEBYTECODE=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_sha=${1:?Pass the exact runtime Git SHA}
evidence=${2:?Pass an evidence directory outside the checkout}
base="${RUNNER_TEMP:-${TMPDIR:-$HOME/agent-work/tmp}}"
# Refuse RAM-backed scratch, including a symlink into tmpfs.
case "$(stat -f -c %T "$base")" in
  tmpfs|ramfs) echo 'Installed journeys require disk scratch' >&2; exit 1 ;;
esac
work="$(mktemp -d "$base/metamuse-installed.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
bootstrap="$(uv python find --managed-python 3.12.13)"
"$bootstrap" -s "$ROOT/scripts/installed_journeys.py" prepare "$ROOT" "$work" "$evidence" "$source_sha"
uv build --directory "$ROOT" --wheel --offline --out-dir "$work/dist"
UV_PROJECT_ENVIRONMENT="$work/venv" uv sync --directory "$ROOT" \
  --locked --offline --python 3.12.13 --group dev --extra remote --extra facebook \
  --no-install-project
shopt -s nullglob
wheels=("$work"/dist/*.whl)
if [[ ${#wheels[@]} -ne 1 ]]; then
  echo "Expected one wheel, found ${#wheels[@]}" >&2
  exit 1
fi
uv pip install --python "$work/venv/bin/python" --offline --no-deps "${wheels[0]}"
cd "$work/run"
"$work/venv/bin/python" -s "$ROOT/scripts/installed_journeys.py" run "$ROOT" "$work" "$evidence" "$source_sha"
