"""Independent graph reachability checks all six seeded propagation triggers."""

import hashlib
import random

import pytest


def h(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.mark.parametrize(
    "kind",
    [
        "account_insert",
        "account_update",
        "vm_insert",
        "vm_update",
        "observation_insert",
        "observation_update",
    ],
)
def test_r6_5_seeded_writes_match_graph_oracle(ctx, kind):
    rng = random.Random(20261014)
    # Two components, with random historical label and observation edges in each.
    edges = {(i, i) for i in range(12)}
    vm_edges = set(edges)
    obs_edges = set(edges)
    for group in (range(6), range(6, 12)):
        for i in group:
            vm_edges.add((i, rng.choice(group)))
            obs_edges.add((i, rng.choice(group)))
    edges |= vm_edges | obs_edges
    with ctx.store.transaction() as conn:
        for i in range(12):
            conn.execute(
                "INSERT INTO muse_trust_accounts VALUES ('p',?,1,?,?,?,1,0,'t')",
                (f"a{i}", h(str(i)), h("s"), f"v{i}"),
            )
        for owner, vm in sorted(vm_edges):
            conn.execute(
                "INSERT INTO muse_trust_vms VALUES ('p',?,?,1,0,'authenticated_assignment','t')",
                (f"a{owner}", f"v{vm}"),
            )
        for owner, vm in sorted(obs_edges):
            conn.execute(
                "INSERT INTO muse_trust_observations VALUES "
                "(?,?,1,0,'authenticated_assignment','t')",
                (h(str(owner)), f"v{vm}"),
            )
        if kind == "account_insert":
            conn.execute(
                "INSERT INTO muse_trust_accounts VALUES ('new','new',1,?,?,'v0',1,1,'t')",
                (h("0"), h("s")),
            )
        elif kind == "account_update":
            conn.execute(
                "UPDATE muse_trust_accounts SET confidential_required=1,generation=generation+1 "
                "WHERE account='a0'"
            )
        elif kind == "vm_insert":
            conn.execute(
                "INSERT INTO muse_trust_vms VALUES ('p','a0','extra',1,1,'noise_message_2','t')"
            )
        elif kind == "vm_update":
            conn.execute(
                "UPDATE muse_trust_vms SET confidential_required=1 "
                "WHERE account='a0' AND vm_id='v0'"
            )
        elif kind == "observation_insert":
            conn.execute(
                "INSERT INTO muse_trust_observations VALUES (?,'extra',1,1,'noise_message_2','t')",
                (h("0"),),
            )
        else:
            conn.execute(
                "UPDATE muse_trust_observations SET confidential_required=1 "
                "WHERE viewer_hash=? AND vm_id='v0'",
                (h("0"),),
            )
        owners, vms = {0}, {0}
        while True:
            old = (owners.copy(), vms.copy())
            vms |= {v for o, v in edges if o in owners}
            owners |= {o for o, v in edges if v in vms}
            if old == (owners, vms):
                break
        for row in conn.execute(
            "SELECT account,confidential_required FROM muse_trust_accounts WHERE provider='p'"
        ):
            assert row[1] == int(int(row[0][1:]) in owners)
        for row in conn.execute(
            "SELECT vm_id,confidential_required FROM muse_trust_vms WHERE vm_id!='extra'"
        ):
            assert row[1] == int(int(row[0][1:]) in vms)
        for row in conn.execute(
            "SELECT viewer_hash,confidential_required FROM muse_trust_observations"
        ):
            assert row[1] == int(row[0] in {h(str(o)) for o in owners})
        assert not conn.execute("SELECT 1 FROM muse_trust_propagating").fetchone()
