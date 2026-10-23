import subprocess
import sys

def test_help_lazy_imports():
    code = """
import sys
import metamuse.cli
try:
    metamuse.cli.main(["--help"])
except SystemExit:
    pass

if "metamuse.app" in sys.modules:
    print("FAILED: app imported")
if "metamuse.models" in sys.modules:
    print("FAILED: models imported")
if "metamuse.jobs" in sys.modules:
    print("FAILED: jobs imported")
print("DONE")
"""
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert "FAILED" not in p.stdout
    assert "DONE" in p.stdout
