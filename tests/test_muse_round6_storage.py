"""SQL boundaries: every component edge, inherit guards, types and lock errnos."""

import ast

import re

from pathlib import Path

import pytest

from test_muse_round5_storage import test_r5_7_fixed_storage_classes as storage_case

from test_muse_trust import freeze

from metamuse.models import CoreError, ErrorCode

@pytest.mark.parametrize("name", ["EACCES", "EPERM", "EIO", "ENOENT"])
@pytest.mark.parametrize("path", ["freeze", "transport", "hint", "rv", "label_busy"])
def test_r6_10_lock_file_access_errno(ctx, monkeypatch, name, path):
    storage_case(ctx, monkeypatch, name, path)

EDGES = [
    owner_to_active_vm,
    owner_to_label_vm,
    owner_to_observed_vm,
    vm_to_active_owner,
    vm_to_label_owner,
    vm_to_observing_owner,
]
