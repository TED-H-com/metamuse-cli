CREATE TABLE projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1),
 created_at TEXT NOT NULL
);
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), operation TEXT NOT NULL,
 request_key TEXT NOT NULL, payload_digest TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','partial','failed','awaiting_user','cancelled')),
 coverage_status TEXT NOT NULL CHECK(coverage_status IN ('unknown','scope_satisfied','bounded','partial')),
 requested_scope TEXT NOT NULL, observed_scope TEXT NOT NULL, stop_reason TEXT,
 missing_fields TEXT NOT NULL, limits TEXT NOT NULL, worker_epoch INTEGER,
 cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
 remote_state TEXT NOT NULL DEFAULT 'unknown', result TEXT, error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
 UNIQUE(operation,request_key), UNIQUE(id,project_id)
);
CREATE TABLE job_steps (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), sequence INTEGER NOT NULL CHECK(sequence>=0),
 target TEXT NOT NULL, checkpoint TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','running','submitting','succeeded','partial','failed','cancelled','open_uncertain','send_uncertain','needs_reconcile')),
 attempts INTEGER NOT NULL CHECK(attempts>=0), retries INTEGER NOT NULL CHECK(retries BETWEEN 0 AND 2),
 side_effect_class TEXT NOT NULL CHECK(side_effect_class IN ('none','safe_read','external_submit')),
 uncertain_state TEXT NOT NULL CHECK(uncertain_state IN ('open_uncertain','send_uncertain','needs_reconcile')),
 worker_epoch INTEGER, result TEXT, error TEXT, UNIQUE(job_id,sequence)
);
CREATE TABLE sources (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), platform TEXT NOT NULL,
 native_scope TEXT, native_id TEXT, original_url TEXT, canonical_url TEXT,
 collection_method TEXT NOT NULL, upstream_collection_method TEXT NOT NULL,
 assurance TEXT NOT NULL, fetched_at TEXT NOT NULL, use_basis TEXT NOT NULL,
 UNIQUE(id,project_id)
);
CREATE TRIGGER source_method_immutable BEFORE UPDATE OF collection_method,upstream_collection_method ON sources
WHEN NEW.collection_method IS NOT OLD.collection_method OR NEW.upstream_collection_method IS NOT OLD.upstream_collection_method
BEGIN SELECT RAISE(ABORT,'immutable acquisition method'); END;
CREATE UNIQUE INDEX source_identity ON sources(project_id,platform,native_scope,native_id)
 WHERE native_scope IS NOT NULL AND native_id IS NOT NULL;
CREATE TABLE items (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), source_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('post','comment','reply')), native_id TEXT, parent_id TEXT,
 unresolved_parent TEXT, identity_confidence TEXT NOT NULL CHECK(identity_confidence IN ('reliable','weak')),
 UNIQUE(id,project_id), FOREIGN KEY(source_id,project_id) REFERENCES sources(id,project_id),
 FOREIGN KEY(parent_id,project_id) REFERENCES items(id,project_id), CHECK(parent_id IS NULL OR parent_id<>id)
);
CREATE UNIQUE INDEX item_identity ON items(source_id,native_id) WHERE native_id IS NOT NULL AND identity_confidence='reliable';
CREATE TRIGGER parent_cycle_insert BEFORE INSERT ON items WHEN NEW.parent_id IS NOT NULL BEGIN
 SELECT RAISE(ABORT,'parent cycle') WHERE EXISTS (
 WITH RECURSIVE ancestors(id) AS (SELECT NEW.parent_id UNION SELECT parent_id FROM items JOIN ancestors ON items.id=ancestors.id WHERE parent_id IS NOT NULL)
 SELECT 1 FROM ancestors WHERE id=NEW.id);
END;
CREATE TRIGGER parent_cycle_update BEFORE UPDATE OF parent_id ON items WHEN NEW.parent_id IS NOT NULL BEGIN
 SELECT RAISE(ABORT,'parent cycle') WHERE EXISTS (
 WITH RECURSIVE ancestors(id) AS (SELECT NEW.parent_id UNION SELECT parent_id FROM items JOIN ancestors ON items.id=ancestors.id WHERE parent_id IS NOT NULL)
 SELECT 1 FROM ancestors WHERE id=NEW.id);
END;
CREATE TABLE item_revisions (
 item_id TEXT NOT NULL REFERENCES items(id), revision INTEGER NOT NULL CHECK(revision>=1), text TEXT,
 text_hash TEXT NOT NULL, raw_time TEXT, parsed_time TEXT, source_time TEXT, fetched_at TEXT NOT NULL,
 complete INTEGER NOT NULL CHECK(complete IN (0,1)), missing_fields TEXT NOT NULL, evidence TEXT NOT NULL,
 PRIMARY KEY(item_id,revision)
);
CREATE TRIGGER item_revision_immutable BEFORE UPDATE ON item_revisions BEGIN SELECT RAISE(ABORT,'append-only revision'); END;
CREATE TABLE assets (
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256)=64), mime TEXT NOT NULL,
 actual_bytes INTEGER NOT NULL CHECK(actual_bytes>=0), duration_ms INTEGER CHECK(duration_ms>=0),
 object_path TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('ready','unavailable'))
);
CREATE TABLE item_assets (
 item_id TEXT NOT NULL, revision INTEGER NOT NULL, asset_id TEXT NOT NULL REFERENCES assets(id), relation TEXT NOT NULL,
 PRIMARY KEY(item_id,revision,asset_id,relation), FOREIGN KEY(item_id,revision) REFERENCES item_revisions(item_id,revision)
);
CREATE TABLE transcript_segments (
 id TEXT NOT NULL, asset_id TEXT NOT NULL REFERENCES assets(id), revision INTEGER NOT NULL CHECK(revision>=1),
 start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, text TEXT NOT NULL, language TEXT NOT NULL,
 method TEXT NOT NULL CHECK(method IN ('provided','auto','asr','manual')), review TEXT NOT NULL,
 PRIMARY KEY(id,revision), CHECK(end_ms>=start_ms AND start_ms>=0)
);
CREATE TABLE packets (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), selection TEXT NOT NULL,
 rendered_text TEXT NOT NULL, redactions TEXT NOT NULL, coverage_status TEXT NOT NULL,
 digest TEXT NOT NULL, created_at TEXT NOT NULL, stale INTEGER NOT NULL CHECK(stale IN (0,1)),
 UNIQUE(id,project_id)
);
CREATE TABLE permits (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), kind TEXT NOT NULL, scope TEXT NOT NULL,
 provider TEXT, account TEXT, issuer TEXT NOT NULL, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 max_uses INTEGER NOT NULL CHECK(typeof(max_uses)='integer' AND max_uses>=1),
 used INTEGER NOT NULL CHECK(typeof(used)='integer' AND used>=0 AND used<=max_uses),
 revoked INTEGER NOT NULL CHECK(revoked IN (0,1)), exact_digest TEXT, UNIQUE(id,project_id)
);
CREATE TRIGGER packet_payload_immutable BEFORE UPDATE OF id,project_id,selection,rendered_text,redactions,coverage_status,digest,created_at ON packets
BEGIN SELECT RAISE(ABORT,'immutable packet payload'); END;
CREATE TRIGGER permit_use_monotonic BEFORE UPDATE OF used ON permits WHEN NEW.used<OLD.used
BEGIN SELECT RAISE(ABORT,'permit use cannot decrease'); END;
CREATE TRIGGER permit_revocation_sticky BEFORE UPDATE OF revoked ON permits WHEN OLD.revoked=1 AND NEW.revoked<>1
BEGIN SELECT RAISE(ABORT,'permit revocation is sticky'); END;
CREATE TRIGGER permit_use_guard BEFORE UPDATE OF used ON permits WHEN NEW.used>OLD.used
BEGIN
 SELECT RAISE(ABORT,'permit cannot be consumed') WHERE OLD.revoked=1 OR NEW.revoked=1
 OR COALESCE(julianday(OLD.expires_at)>julianday(metamuse_now()),0)=0;
END;
CREATE TRIGGER permit_scope_immutable BEFORE UPDATE OF id,project_id,kind,scope,provider,account,issuer,issued_at,expires_at,max_uses,exact_digest ON permits
BEGIN SELECT RAISE(ABORT,'immutable permit scope'); END;
CREATE TABLE discussions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), provider TEXT NOT NULL,
 auth_ref TEXT NOT NULL, topic TEXT NOT NULL, remote_session TEXT, open_attempt_state TEXT NOT NULL,
 last_seq INTEGER NOT NULL, state TEXT NOT NULL, UNIQUE(id,project_id)
);
CREATE TABLE exchanges (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), discussion_id TEXT NOT NULL,
 job_id TEXT, request_key TEXT NOT NULL, question TEXT NOT NULL, packet_id TEXT NOT NULL, digest TEXT NOT NULL,
 permit_id TEXT NOT NULL, baseline TEXT NOT NULL, state TEXT NOT NULL,
 outcome TEXT CHECK(outcome IN ('answered','needs_more_data','abandoned')),
 FOREIGN KEY(discussion_id,project_id) REFERENCES discussions(id,project_id),
 FOREIGN KEY(packet_id,project_id) REFERENCES packets(id,project_id),
 FOREIGN KEY(permit_id,project_id) REFERENCES permits(id,project_id),
 FOREIGN KEY(job_id,project_id) REFERENCES jobs(id,project_id), UNIQUE(discussion_id,request_key),
 UNIQUE(id,discussion_id)
);
CREATE UNIQUE INDEX one_open_exchange ON exchanges(discussion_id) WHERE outcome IS NULL;
CREATE TRIGGER exchange_outcome_sticky BEFORE UPDATE OF outcome ON exchanges
WHEN OLD.outcome IS NOT NULL AND NEW.outcome IS NOT OLD.outcome
BEGIN SELECT RAISE(ABORT,'exchange outcome is sticky'); END;
CREATE TABLE messages (
 id TEXT PRIMARY KEY, discussion_id TEXT NOT NULL REFERENCES discussions(id), exchange_id TEXT,
 remote_session TEXT NOT NULL, remote_message_id TEXT, seq INTEGER, role TEXT NOT NULL,
 raw_status TEXT, text TEXT NOT NULL, association TEXT NOT NULL, observed_at TEXT NOT NULL,
 FOREIGN KEY(exchange_id,discussion_id) REFERENCES exchanges(id,discussion_id)
);
CREATE UNIQUE INDEX message_identity ON messages(remote_session,remote_message_id) WHERE remote_message_id IS NOT NULL;
CREATE TABLE drafts (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), current_revision INTEGER NOT NULL CHECK(current_revision>=1),
 stale INTEGER NOT NULL CHECK(stale IN (0,1))
);
CREATE TABLE draft_revisions (
 draft_id TEXT NOT NULL REFERENCES drafts(id), revision INTEGER NOT NULL CHECK(revision>=1), text TEXT NOT NULL,
 platform TEXT NOT NULL, variant TEXT NOT NULL, facts_refs TEXT NOT NULL, assets TEXT NOT NULL,
 preferences TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(draft_id,revision)
);
CREATE TRIGGER draft_revision_immutable BEFORE UPDATE ON draft_revisions BEGIN SELECT RAISE(ABORT,'append-only revision'); END;
CREATE TRIGGER draft_revision_sequence BEFORE INSERT ON draft_revisions
BEGIN
 SELECT RAISE(ABORT,'draft revision must append to current head') WHERE
 NEW.revision<>COALESCE((SELECT MAX(revision)+1 FROM draft_revisions WHERE draft_id=NEW.draft_id),1)
 OR (SELECT current_revision FROM drafts WHERE id=NEW.draft_id)<>MAX(1,NEW.revision-1);
END;
CREATE TRIGGER draft_revision_head AFTER INSERT ON draft_revisions
BEGIN UPDATE drafts SET current_revision=NEW.revision WHERE id=NEW.draft_id; END;
CREATE TRIGGER draft_head_guard BEFORE UPDATE OF current_revision ON drafts
WHEN NEW.current_revision<OLD.current_revision OR NOT EXISTS (
 SELECT 1 FROM draft_revisions WHERE draft_id=OLD.id AND revision=NEW.current_revision)
BEGIN SELECT RAISE(ABORT,'draft head requires an appended revision'); END;
CREATE TABLE exports (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), selection TEXT NOT NULL,
 format TEXT NOT NULL, manifest TEXT NOT NULL, artifact TEXT, state TEXT NOT NULL
);
CREATE TABLE events (
 id TEXT PRIMARY KEY, object_id TEXT, job_id TEXT REFERENCES jobs(id), project_id TEXT REFERENCES projects(id),
 kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE worker_state (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner_nonce TEXT, worker_epoch INTEGER NOT NULL,
 heartbeat TEXT
);
INSERT INTO worker_state VALUES(1,NULL,0,NULL);
-- Synchronous application mutations share durable idempotency receipts, not a second queue.
CREATE TABLE request_receipts (
 operation TEXT NOT NULL, request_key TEXT NOT NULL, payload_digest TEXT NOT NULL, response TEXT NOT NULL,
 PRIMARY KEY(operation,request_key)
);
CREATE TRIGGER external_step_no_requeue BEFORE UPDATE OF state ON job_steps
WHEN OLD.side_effect_class='external_submit' AND OLD.attempts>0 AND NEW.state='queued'
BEGIN SELECT RAISE(ABORT,'entered external step cannot be replayed'); END;
CREATE TRIGGER external_step_attempts_monotonic BEFORE UPDATE OF attempts,side_effect_class ON job_steps
WHEN OLD.side_effect_class='external_submit' AND OLD.attempts>0
 AND (NEW.attempts<OLD.attempts OR NEW.side_effect_class<>OLD.side_effect_class)
BEGIN SELECT RAISE(ABORT,'external attempt identity cannot be reset'); END;
