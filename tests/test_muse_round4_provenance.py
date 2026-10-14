"""Once confidential, provenance remains frozen; subsequent freezes cannot restamp it."""

import pytest
from test_muse_trust import freeze

from metamuse.models import CoreError


def test_r4_12_rv_provenance_survives_standard_and_direct_refreeze(ctx):
    snapshot = freeze(ctx.store)
    ctx.store.muse_record_confidential(snapshot)
    with ctx.store.connection() as conn:
        initial = dict(
            conn.execute("SELECT source,observed_at FROM muse_trust_observations").fetchone()
        )
    assert initial["source"] == "noise_message_2"
    for direct in (False, True):
        assert freeze(ctx.store, confidential=direct).confidential_required
        ctx.store.muse_record_confidential(snapshot)
        with ctx.store.connection() as conn:
            assert (
                dict(
                    conn.execute(
                        "SELECT source,observed_at FROM muse_trust_observations"
                    ).fetchone()
                )
                == initial
            )


@pytest.mark.parametrize("table", ["muse_trust_observations", "muse_trust_vms"])
def test_r4_12_sql_cannot_restamp_confidential_provenance(ctx, table):
    freeze(ctx.store, confidential=True)
    with pytest.raises(CoreError):
        with ctx.store.transaction() as conn:
            conn.execute(f"UPDATE {table} SET source='noise_message_2',observed_at='later'")
