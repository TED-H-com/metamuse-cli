-- B09: private account/VM trust, independent of rotating auth_ref. No secrets.
CREATE TABLE muse_trust_accounts (
    provider TEXT NOT NULL CHECK(typeof(provider)='text'),
    account TEXT NOT NULL CHECK(typeof(account)='text'),
    version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version = 1),
    viewer_hash TEXT NOT NULL CHECK(typeof(viewer_hash)='text' AND length(viewer_hash) = 64 AND viewer_hash NOT GLOB '*[^0-9a-f]*'),
    session_hash TEXT NOT NULL CHECK(typeof(session_hash)='text' AND length(session_hash) = 64 AND session_hash NOT GLOB '*[^0-9a-f]*'),
    active_vm_id TEXT CHECK((active_vm_id IS NULL OR typeof(active_vm_id)='text') AND (active_vm_id IS NULL AND confidential_required = 1) OR (active_vm_id IS NOT NULL AND length(active_vm_id) BETWEEN 1 AND 128 AND active_vm_id = lower(trim(active_vm_id)) AND active_vm_id NOT GLOB '*[^a-z0-9._-]*')),
    generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation >= 1),
    confidential_required INTEGER NOT NULL CHECK(typeof(confidential_required)='integer' AND confidential_required IN (0, 1)),
    updated_at TEXT NOT NULL CHECK(typeof(updated_at)='text' AND length(updated_at)>0),
    PRIMARY KEY(provider, account)
);
CREATE TABLE muse_trust_vms (
    provider TEXT NOT NULL CHECK(typeof(provider)='text'),
    account TEXT NOT NULL CHECK(typeof(account)='text'),
    vm_id TEXT NOT NULL CHECK(typeof(vm_id)='text' AND length(vm_id) BETWEEN 1 AND 128 AND vm_id = lower(trim(vm_id)) AND vm_id NOT GLOB '*[^a-z0-9._-]*'),
    version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version = 1),
    confidential_required INTEGER NOT NULL CHECK(typeof(confidential_required)='integer' AND confidential_required IN (0, 1)),
    source TEXT NOT NULL CHECK(typeof(source)='text' AND source IN ('authenticated_assignment', 'noise_message_2')),
    observed_at TEXT NOT NULL CHECK(typeof(observed_at)='text' AND length(observed_at)>0),
    PRIMARY KEY(provider, account, vm_id),
    FOREIGN KEY(provider, account) REFERENCES muse_trust_accounts(provider, account)
);
CREATE TRIGGER muse_trust_account_no_replace BEFORE INSERT ON muse_trust_accounts
WHEN EXISTS(SELECT 1 FROM muse_trust_accounts WHERE
    (provider = NEW.provider AND account = NEW.account) OR rowid = NEW.rowid)
BEGIN SELECT RAISE(ABORT, 'muse trust account replacement'); END;
CREATE TRIGGER muse_trust_account_guard BEFORE UPDATE ON muse_trust_accounts
WHEN NEW.rowid != OLD.rowid OR NEW.provider != OLD.provider OR NEW.account != OLD.account
    OR NEW.version != OLD.version OR NEW.viewer_hash != OLD.viewer_hash
    OR NEW.generation < OLD.generation OR NEW.confidential_required < OLD.confidential_required
    OR ((NEW.active_vm_id IS NOT OLD.active_vm_id OR NEW.session_hash != OLD.session_hash
         OR NEW.confidential_required != OLD.confidential_required) AND NEW.generation <= OLD.generation)
BEGIN SELECT RAISE(ABORT, 'muse trust account immutable or stale'); END;
CREATE TRIGGER muse_trust_account_retention BEFORE DELETE ON muse_trust_accounts
BEGIN SELECT RAISE(ABORT, 'muse trust account retention'); END;
CREATE TRIGGER muse_trust_vm_no_replace BEFORE INSERT ON muse_trust_vms
WHEN EXISTS(SELECT 1 FROM muse_trust_vms WHERE
    (provider = NEW.provider AND account = NEW.account AND vm_id = NEW.vm_id) OR rowid = NEW.rowid)
BEGIN SELECT RAISE(ABORT, 'muse trust VM replacement'); END;
CREATE TRIGGER muse_trust_vm_guard BEFORE UPDATE ON muse_trust_vms
WHEN NEW.rowid != OLD.rowid OR NEW.provider != OLD.provider OR NEW.account != OLD.account
    OR NEW.vm_id != OLD.vm_id OR NEW.version != OLD.version
    OR NEW.confidential_required < OLD.confidential_required
    OR (OLD.confidential_required=1 AND (NEW.source IS NOT OLD.source OR NEW.observed_at IS NOT OLD.observed_at))
BEGIN SELECT RAISE(ABORT, 'muse trust VM immutable or downgrade'); END;
CREATE TRIGGER muse_trust_vm_retention BEFORE DELETE ON muse_trust_vms
BEGIN SELECT RAISE(ABORT, 'muse trust VM retention'); END;

CREATE TRIGGER muse_trust_accounts_inherit_insert BEFORE INSERT ON muse_trust_accounts
WHEN NEW.confidential_required=0 AND EXISTS(WITH RECURSIVE component(kind,identity) AS (
 SELECT 'owner', NEW.viewer_hash
 UNION SELECT 'vm', NEW.active_vm_id WHERE NEW.active_vm_id IS NOT NULL AND NEW.active_vm_id!=''
 UNION SELECT 'vm',a.active_vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity WHERE a.active_vm_id IS NOT NULL
 UNION SELECT 'vm',v.vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity JOIN muse_trust_vms v USING(provider,account)
 UNION SELECT 'vm',o.vm_id FROM component r JOIN muse_trust_observations o ON r.kind='owner' AND o.viewer_hash=r.identity WHERE o.vm_id!=''
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_accounts a ON r.kind='vm' AND a.active_vm_id=r.identity
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_vms v ON r.kind='vm' AND v.vm_id=r.identity JOIN muse_trust_accounts a USING(provider,account)
 UNION SELECT 'owner',o.viewer_hash FROM component r JOIN muse_trust_observations o ON r.kind='vm' AND o.vm_id=r.identity WHERE o.vm_id!=''
)
SELECT 1 FROM component r WHERE
 (r.kind='owner' AND (EXISTS(SELECT 1 FROM muse_trust_accounts a WHERE a.viewer_hash=r.identity AND a.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.viewer_hash=r.identity AND o.confidential_required=1)))
 OR (r.kind='vm' AND (EXISTS(SELECT 1 FROM muse_trust_vms v WHERE v.vm_id=r.identity AND v.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.vm_id=r.identity AND o.confidential_required=1))) LIMIT 1)
BEGIN SELECT RAISE(ABORT, 'muse trust inherited confidentiality'); END;
CREATE TRIGGER muse_trust_accounts_propagate_insert AFTER INSERT ON muse_trust_accounts
WHEN NEW.confidential_required=1 AND NOT EXISTS(SELECT 1 FROM muse_trust_propagating)
BEGIN
INSERT INTO muse_trust_propagating VALUES (1);
UPDATE muse_trust_accounts SET confidential_required=1,generation=generation+1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
UPDATE muse_trust_vms SET confidential_required=1 WHERE confidential_required=0 AND vm_id IN (SELECT identity FROM muse_trust_confidential WHERE kind='vm');
UPDATE muse_trust_observations SET confidential_required=1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
DELETE FROM muse_trust_propagating;
END;

CREATE TRIGGER muse_trust_accounts_inherit_update BEFORE UPDATE ON muse_trust_accounts
WHEN NEW.confidential_required=0 AND EXISTS(WITH RECURSIVE component(kind,identity) AS (
 SELECT 'owner', NEW.viewer_hash
 UNION SELECT 'vm', NEW.active_vm_id WHERE NEW.active_vm_id IS NOT NULL AND NEW.active_vm_id!=''
 UNION SELECT 'vm',a.active_vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity WHERE a.active_vm_id IS NOT NULL
 UNION SELECT 'vm',v.vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity JOIN muse_trust_vms v USING(provider,account)
 UNION SELECT 'vm',o.vm_id FROM component r JOIN muse_trust_observations o ON r.kind='owner' AND o.viewer_hash=r.identity WHERE o.vm_id!=''
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_accounts a ON r.kind='vm' AND a.active_vm_id=r.identity
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_vms v ON r.kind='vm' AND v.vm_id=r.identity JOIN muse_trust_accounts a USING(provider,account)
 UNION SELECT 'owner',o.viewer_hash FROM component r JOIN muse_trust_observations o ON r.kind='vm' AND o.vm_id=r.identity WHERE o.vm_id!=''
)
SELECT 1 FROM component r WHERE
 (r.kind='owner' AND (EXISTS(SELECT 1 FROM muse_trust_accounts a WHERE a.viewer_hash=r.identity AND a.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.viewer_hash=r.identity AND o.confidential_required=1)))
 OR (r.kind='vm' AND (EXISTS(SELECT 1 FROM muse_trust_vms v WHERE v.vm_id=r.identity AND v.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.vm_id=r.identity AND o.confidential_required=1))) LIMIT 1)
BEGIN SELECT RAISE(ABORT, 'muse trust inherited confidentiality'); END;
CREATE TRIGGER muse_trust_accounts_propagate_update AFTER UPDATE ON muse_trust_accounts
WHEN NEW.confidential_required=1 AND OLD.confidential_required=0 AND NOT EXISTS(SELECT 1 FROM muse_trust_propagating)
BEGIN
INSERT INTO muse_trust_propagating VALUES (1);
UPDATE muse_trust_accounts SET confidential_required=1,generation=generation+1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
UPDATE muse_trust_vms SET confidential_required=1 WHERE confidential_required=0 AND vm_id IN (SELECT identity FROM muse_trust_confidential WHERE kind='vm');
UPDATE muse_trust_observations SET confidential_required=1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
DELETE FROM muse_trust_propagating;
END;

CREATE TRIGGER muse_trust_vms_inherit_insert BEFORE INSERT ON muse_trust_vms
WHEN NEW.confidential_required=0 AND EXISTS(WITH RECURSIVE component(kind,identity) AS (
 SELECT 'owner', (SELECT viewer_hash FROM muse_trust_accounts WHERE provider=NEW.provider AND account=NEW.account)
 UNION SELECT 'vm', NEW.vm_id WHERE NEW.vm_id IS NOT NULL AND NEW.vm_id!=''
 UNION SELECT 'vm',a.active_vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity WHERE a.active_vm_id IS NOT NULL
 UNION SELECT 'vm',v.vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity JOIN muse_trust_vms v USING(provider,account)
 UNION SELECT 'vm',o.vm_id FROM component r JOIN muse_trust_observations o ON r.kind='owner' AND o.viewer_hash=r.identity WHERE o.vm_id!=''
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_accounts a ON r.kind='vm' AND a.active_vm_id=r.identity
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_vms v ON r.kind='vm' AND v.vm_id=r.identity JOIN muse_trust_accounts a USING(provider,account)
 UNION SELECT 'owner',o.viewer_hash FROM component r JOIN muse_trust_observations o ON r.kind='vm' AND o.vm_id=r.identity WHERE o.vm_id!=''
)
SELECT 1 FROM component r WHERE
 (r.kind='owner' AND (EXISTS(SELECT 1 FROM muse_trust_accounts a WHERE a.viewer_hash=r.identity AND a.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.viewer_hash=r.identity AND o.confidential_required=1)))
 OR (r.kind='vm' AND (EXISTS(SELECT 1 FROM muse_trust_vms v WHERE v.vm_id=r.identity AND v.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.vm_id=r.identity AND o.confidential_required=1))) LIMIT 1)
BEGIN SELECT RAISE(ABORT, 'muse trust inherited confidentiality'); END;
CREATE TRIGGER muse_trust_vms_propagate_insert AFTER INSERT ON muse_trust_vms
WHEN NEW.confidential_required=1 AND NOT EXISTS(SELECT 1 FROM muse_trust_propagating)
BEGIN
INSERT INTO muse_trust_propagating VALUES (1);
UPDATE muse_trust_accounts SET confidential_required=1,generation=generation+1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
UPDATE muse_trust_vms SET confidential_required=1 WHERE confidential_required=0 AND vm_id IN (SELECT identity FROM muse_trust_confidential WHERE kind='vm');
UPDATE muse_trust_observations SET confidential_required=1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
DELETE FROM muse_trust_propagating;
END;

CREATE TRIGGER muse_trust_vms_inherit_update BEFORE UPDATE ON muse_trust_vms
WHEN NEW.confidential_required=0 AND EXISTS(WITH RECURSIVE component(kind,identity) AS (
 SELECT 'owner', (SELECT viewer_hash FROM muse_trust_accounts WHERE provider=NEW.provider AND account=NEW.account)
 UNION SELECT 'vm', NEW.vm_id WHERE NEW.vm_id IS NOT NULL AND NEW.vm_id!=''
 UNION SELECT 'vm',a.active_vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity WHERE a.active_vm_id IS NOT NULL
 UNION SELECT 'vm',v.vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity JOIN muse_trust_vms v USING(provider,account)
 UNION SELECT 'vm',o.vm_id FROM component r JOIN muse_trust_observations o ON r.kind='owner' AND o.viewer_hash=r.identity WHERE o.vm_id!=''
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_accounts a ON r.kind='vm' AND a.active_vm_id=r.identity
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_vms v ON r.kind='vm' AND v.vm_id=r.identity JOIN muse_trust_accounts a USING(provider,account)
 UNION SELECT 'owner',o.viewer_hash FROM component r JOIN muse_trust_observations o ON r.kind='vm' AND o.vm_id=r.identity WHERE o.vm_id!=''
)
SELECT 1 FROM component r WHERE
 (r.kind='owner' AND (EXISTS(SELECT 1 FROM muse_trust_accounts a WHERE a.viewer_hash=r.identity AND a.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.viewer_hash=r.identity AND o.confidential_required=1)))
 OR (r.kind='vm' AND (EXISTS(SELECT 1 FROM muse_trust_vms v WHERE v.vm_id=r.identity AND v.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.vm_id=r.identity AND o.confidential_required=1))) LIMIT 1)
BEGIN SELECT RAISE(ABORT, 'muse trust inherited confidentiality'); END;
CREATE TRIGGER muse_trust_vms_propagate_update AFTER UPDATE ON muse_trust_vms
WHEN NEW.confidential_required=1 AND OLD.confidential_required=0 AND NOT EXISTS(SELECT 1 FROM muse_trust_propagating)
BEGIN
INSERT INTO muse_trust_propagating VALUES (1);
UPDATE muse_trust_accounts SET confidential_required=1,generation=generation+1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
UPDATE muse_trust_vms SET confidential_required=1 WHERE confidential_required=0 AND vm_id IN (SELECT identity FROM muse_trust_confidential WHERE kind='vm');
UPDATE muse_trust_observations SET confidential_required=1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
DELETE FROM muse_trust_propagating;
END;

CREATE INDEX muse_trust_active_vm ON muse_trust_accounts(active_vm_id);
CREATE INDEX muse_trust_owner_expectation ON muse_trust_accounts(viewer_hash);
CREATE INDEX muse_trust_vm_expectation ON muse_trust_vms(vm_id);

-- Remote authenticated observations are independent of local connection labels.
-- Empty VM id represents owner-only evidence; it is never a wire VM id.
CREATE TABLE muse_trust_observations (
    viewer_hash TEXT NOT NULL CHECK(typeof(viewer_hash)='text' AND length(viewer_hash)=64 AND viewer_hash NOT GLOB '*[^0-9a-f]*'),
    vm_id TEXT NOT NULL CHECK(typeof(vm_id)='text' AND vm_id='' OR (length(vm_id) BETWEEN 1 AND 128 AND vm_id=lower(trim(vm_id)) AND vm_id NOT GLOB '*[^a-z0-9._-]*')),
    version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version=1),
    confidential_required INTEGER NOT NULL CHECK(typeof(confidential_required)='integer' AND confidential_required IN (0,1)),
    source TEXT NOT NULL CHECK(typeof(source)='text' AND source IN ('authenticated_assignment','noise_message_2')),
    observed_at TEXT NOT NULL CHECK(typeof(observed_at)='text' AND length(observed_at)>0),
    PRIMARY KEY(viewer_hash, vm_id)
);
CREATE INDEX muse_trust_observation_vm ON muse_trust_observations(vm_id);
CREATE TRIGGER muse_trust_observation_no_replace BEFORE INSERT ON muse_trust_observations
WHEN EXISTS(SELECT 1 FROM muse_trust_observations WHERE (viewer_hash=NEW.viewer_hash AND vm_id=NEW.vm_id) OR rowid=NEW.rowid)
BEGIN SELECT RAISE(ABORT, 'muse trust observation replacement'); END;
CREATE TRIGGER muse_trust_observation_guard BEFORE UPDATE ON muse_trust_observations
WHEN NEW.rowid != OLD.rowid OR NEW.viewer_hash != OLD.viewer_hash OR NEW.vm_id != OLD.vm_id OR NEW.version != OLD.version OR NEW.confidential_required < OLD.confidential_required
    OR (OLD.confidential_required=1 AND (NEW.source IS NOT OLD.source OR NEW.observed_at IS NOT OLD.observed_at))
BEGIN SELECT RAISE(ABORT, 'muse trust observation immutable or downgrade'); END;
CREATE TRIGGER muse_trust_observation_retention BEFORE DELETE ON muse_trust_observations
BEGIN SELECT RAISE(ABORT, 'muse trust observation retention'); END;
CREATE TRIGGER muse_trust_observations_propagate_insert AFTER INSERT ON muse_trust_observations
WHEN NEW.confidential_required=1 AND NOT EXISTS(SELECT 1 FROM muse_trust_propagating)
BEGIN
INSERT INTO muse_trust_propagating VALUES (1);
UPDATE muse_trust_accounts SET confidential_required=1,generation=generation+1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
UPDATE muse_trust_vms SET confidential_required=1 WHERE confidential_required=0 AND vm_id IN (SELECT identity FROM muse_trust_confidential WHERE kind='vm');
UPDATE muse_trust_observations SET confidential_required=1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
DELETE FROM muse_trust_propagating;
END;
CREATE TRIGGER muse_trust_observations_propagate_update AFTER UPDATE ON muse_trust_observations
WHEN NEW.confidential_required=1 AND OLD.confidential_required=0 AND NOT EXISTS(SELECT 1 FROM muse_trust_propagating)
BEGIN
INSERT INTO muse_trust_propagating VALUES (1);
UPDATE muse_trust_accounts SET confidential_required=1,generation=generation+1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
UPDATE muse_trust_vms SET confidential_required=1 WHERE confidential_required=0 AND vm_id IN (SELECT identity FROM muse_trust_confidential WHERE kind='vm');
UPDATE muse_trust_observations SET confidential_required=1 WHERE confidential_required=0 AND viewer_hash IN (SELECT identity FROM muse_trust_confidential WHERE kind='owner');
DELETE FROM muse_trust_propagating;
END;

-- A transaction-local recursion barrier: propagation updates a whole closure once.
-- Bulk authenticated hint binds insert this barrier before any observation/VM row,
-- materialize the closure once, update all three tables, then delete it before commit.
-- No committed row is permitted here; every Store boundary checks it is empty.
CREATE TABLE muse_trust_propagating (id INTEGER PRIMARY KEY CHECK(id=1));
CREATE VIEW muse_trust_confidential AS
WITH RECURSIVE edges(owner,vm) AS (
 SELECT viewer_hash,active_vm_id FROM muse_trust_accounts WHERE active_vm_id IS NOT NULL
 UNION SELECT a.viewer_hash,v.vm_id FROM muse_trust_vms v JOIN muse_trust_accounts a USING(provider,account)
 UNION SELECT viewer_hash,vm_id FROM muse_trust_observations WHERE vm_id!=''
), required(kind,identity) AS (
 SELECT 'owner',viewer_hash FROM muse_trust_accounts WHERE confidential_required=1
 UNION SELECT 'vm',vm_id FROM muse_trust_vms WHERE confidential_required=1
 UNION SELECT 'owner',viewer_hash FROM muse_trust_observations WHERE confidential_required=1
 UNION SELECT 'vm',vm_id FROM muse_trust_observations WHERE confidential_required=1 AND vm_id!=''
 UNION SELECT 'vm',e.vm FROM required r JOIN edges e ON r.kind='owner' AND r.identity=e.owner
 UNION SELECT 'owner',e.owner FROM required r JOIN edges e ON r.kind='vm' AND r.identity=e.vm
)
SELECT kind,identity FROM required;

CREATE TRIGGER muse_trust_observations_inherit_insert BEFORE INSERT ON muse_trust_observations
WHEN NEW.confidential_required=0 AND EXISTS(WITH RECURSIVE component(kind,identity) AS (
 SELECT 'owner', NEW.viewer_hash
 UNION SELECT 'vm', NEW.vm_id WHERE NEW.vm_id IS NOT NULL AND NEW.vm_id!=''
 UNION SELECT 'vm',a.active_vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity WHERE a.active_vm_id IS NOT NULL
 UNION SELECT 'vm',v.vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity JOIN muse_trust_vms v USING(provider,account)
 UNION SELECT 'vm',o.vm_id FROM component r JOIN muse_trust_observations o ON r.kind='owner' AND o.viewer_hash=r.identity WHERE o.vm_id!=''
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_accounts a ON r.kind='vm' AND a.active_vm_id=r.identity
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_vms v ON r.kind='vm' AND v.vm_id=r.identity JOIN muse_trust_accounts a USING(provider,account)
 UNION SELECT 'owner',o.viewer_hash FROM component r JOIN muse_trust_observations o ON r.kind='vm' AND o.vm_id=r.identity WHERE o.vm_id!=''
)
SELECT 1 FROM component r WHERE
 (r.kind='owner' AND (EXISTS(SELECT 1 FROM muse_trust_accounts a WHERE a.viewer_hash=r.identity AND a.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.viewer_hash=r.identity AND o.confidential_required=1)))
 OR (r.kind='vm' AND (EXISTS(SELECT 1 FROM muse_trust_vms v WHERE v.vm_id=r.identity AND v.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.vm_id=r.identity AND o.confidential_required=1))) LIMIT 1)
BEGIN SELECT RAISE(ABORT, 'muse trust inherited confidentiality'); END;

CREATE TRIGGER muse_trust_observations_inherit_update BEFORE UPDATE ON muse_trust_observations
WHEN NEW.confidential_required=0 AND EXISTS(WITH RECURSIVE component(kind,identity) AS (
 SELECT 'owner', NEW.viewer_hash
 UNION SELECT 'vm', NEW.vm_id WHERE NEW.vm_id IS NOT NULL AND NEW.vm_id!=''
 UNION SELECT 'vm',a.active_vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity WHERE a.active_vm_id IS NOT NULL
 UNION SELECT 'vm',v.vm_id FROM component r JOIN muse_trust_accounts a ON r.kind='owner' AND a.viewer_hash=r.identity JOIN muse_trust_vms v USING(provider,account)
 UNION SELECT 'vm',o.vm_id FROM component r JOIN muse_trust_observations o ON r.kind='owner' AND o.viewer_hash=r.identity WHERE o.vm_id!=''
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_accounts a ON r.kind='vm' AND a.active_vm_id=r.identity
 UNION SELECT 'owner',a.viewer_hash FROM component r JOIN muse_trust_vms v ON r.kind='vm' AND v.vm_id=r.identity JOIN muse_trust_accounts a USING(provider,account)
 UNION SELECT 'owner',o.viewer_hash FROM component r JOIN muse_trust_observations o ON r.kind='vm' AND o.vm_id=r.identity WHERE o.vm_id!=''
)
SELECT 1 FROM component r WHERE
 (r.kind='owner' AND (EXISTS(SELECT 1 FROM muse_trust_accounts a WHERE a.viewer_hash=r.identity AND a.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.viewer_hash=r.identity AND o.confidential_required=1)))
 OR (r.kind='vm' AND (EXISTS(SELECT 1 FROM muse_trust_vms v WHERE v.vm_id=r.identity AND v.confidential_required=1) OR EXISTS(SELECT 1 FROM muse_trust_observations o WHERE o.vm_id=r.identity AND o.confidential_required=1))) LIMIT 1)
BEGIN SELECT RAISE(ABORT, 'muse trust inherited confidentiality'); END;
