-- B09: private account/VM trust, independent of rotating auth_ref. No secrets.
CREATE TABLE muse_trust_accounts (
    provider TEXT NOT NULL,
    account TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version = 1),
    viewer_hash TEXT NOT NULL CHECK(length(viewer_hash) = 64),
    session_hash TEXT NOT NULL CHECK(length(session_hash) = 64),
    active_vm_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation >= 1),
    confidential_required INTEGER NOT NULL CHECK(confidential_required IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(provider, account)
);
CREATE TABLE muse_trust_vms (
    provider TEXT NOT NULL,
    account TEXT NOT NULL,
    vm_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version = 1),
    confidential_required INTEGER NOT NULL CHECK(confidential_required IN (0, 1)),
    source TEXT NOT NULL CHECK(source IN ('authenticated_assignment', 'noise_message_2')),
    observed_at TEXT NOT NULL,
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
    OR ((NEW.active_vm_id != OLD.active_vm_id OR NEW.session_hash != OLD.session_hash
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
BEGIN SELECT RAISE(ABORT, 'muse trust VM immutable or downgrade'); END;
CREATE TRIGGER muse_trust_vm_retention BEFORE DELETE ON muse_trust_vms
BEGIN SELECT RAISE(ABORT, 'muse trust VM retention'); END;
