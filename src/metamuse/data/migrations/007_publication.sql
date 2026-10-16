CREATE TABLE publications (
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 draft_id TEXT NOT NULL REFERENCES drafts(id),
 revision INTEGER NOT NULL CHECK(revision>=1),
 account TEXT NOT NULL,
 destination TEXT NOT NULL,
 action_digest TEXT NOT NULL UNIQUE,
 target TEXT NOT NULL CHECK(json_valid(target)),
 payload TEXT NOT NULL CHECK(json_valid(payload)),
 job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
 step_id TEXT NOT NULL UNIQUE REFERENCES job_steps(id),
 state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','not_submitted','submitting','needs_reconcile','sent_verified')),
 remote_id TEXT,
 created_at TEXT NOT NULL,
 verified_at TEXT,
 FOREIGN KEY(draft_id,revision) REFERENCES draft_revisions(draft_id,revision)
);
CREATE UNIQUE INDEX publication_unresolved ON publications(draft_id,account,destination)
 WHERE state NOT IN ('sent_verified','not_submitted');
CREATE TABLE publication_attempts (
 id TEXT PRIMARY KEY,
 publication_id TEXT NOT NULL UNIQUE REFERENCES publications(id),
 step_id TEXT NOT NULL UNIQUE REFERENCES job_steps(id),
 action_digest TEXT NOT NULL,
 started_at TEXT NOT NULL,
 remote_id TEXT
);
CREATE TRIGGER publication_identity_immutable BEFORE UPDATE ON publications
 WHEN NEW.rowid IS NOT OLD.rowid
 OR NEW.id!=OLD.id OR NEW.project_id!=OLD.project_id OR NEW.draft_id!=OLD.draft_id
 OR NEW.revision!=OLD.revision OR NEW.account!=OLD.account OR NEW.destination!=OLD.destination
 OR NEW.action_digest!=OLD.action_digest OR NEW.target!=OLD.target OR NEW.payload!=OLD.payload
 OR NEW.created_at!=OLD.created_at
 OR ((NEW.job_id!=OLD.job_id OR NEW.step_id!=OLD.step_id) AND
     (EXISTS(SELECT 1 FROM publication_attempts WHERE publication_id=OLD.id)
      OR NOT EXISTS(SELECT 1 FROM jobs WHERE id=OLD.job_id AND remote_state='not_submitted'
                    AND state IN ('failed','awaiting_user','cancelled'))))
 OR (NEW.state='not_submitted' AND EXISTS(SELECT 1 FROM publication_attempts WHERE publication_id=OLD.id))
 OR (OLD.remote_id IS NOT NULL AND NEW.remote_id IS NOT OLD.remote_id)
 OR (OLD.state='sent_verified' AND NEW.state!='sent_verified')
 BEGIN SELECT RAISE(ABORT,'immutable publication'); END;
CREATE TRIGGER publication_attempt_immutable BEFORE UPDATE ON publication_attempts
 WHEN NEW.rowid IS NOT OLD.rowid
 OR NEW.id!=OLD.id OR NEW.publication_id!=OLD.publication_id OR NEW.step_id!=OLD.step_id
 OR NEW.action_digest!=OLD.action_digest OR NEW.started_at!=OLD.started_at
 OR (OLD.remote_id IS NOT NULL AND NEW.remote_id IS NOT OLD.remote_id)
 BEGIN SELECT RAISE(ABORT,'immutable publication attempt'); END;
CREATE TRIGGER publication_no_replace BEFORE INSERT ON publications
 WHEN EXISTS(SELECT 1 FROM publications WHERE rowid=NEW.rowid OR id=NEW.id
 OR action_digest=NEW.action_digest OR job_id=NEW.job_id OR step_id=NEW.step_id
 OR (draft_id=NEW.draft_id AND account=NEW.account AND destination=NEW.destination
     AND state NOT IN ('sent_verified','not_submitted')
     AND NEW.state NOT IN ('sent_verified','not_submitted')))
 BEGIN SELECT RAISE(ABORT,'publication identity retained'); END;
CREATE TRIGGER publication_attempt_no_replace BEFORE INSERT ON publication_attempts
 WHEN EXISTS(SELECT 1 FROM publication_attempts WHERE rowid=NEW.rowid OR id=NEW.id
 OR publication_id=NEW.publication_id OR step_id=NEW.step_id)
 BEGIN SELECT RAISE(ABORT,'publication attempt retained'); END;
CREATE TRIGGER publication_retained BEFORE DELETE ON publications
 BEGIN SELECT RAISE(ABORT,'publication retained'); END;
CREATE TRIGGER publication_attempt_retained BEFORE DELETE ON publication_attempts
 BEGIN SELECT RAISE(ABORT,'publication attempt retained'); END;
CREATE TRIGGER publication_owner_insert BEFORE INSERT ON publications
 WHEN NOT EXISTS(
 SELECT 1 FROM drafts d JOIN draft_revisions r ON r.draft_id=d.id
 JOIN jobs j ON j.id=NEW.job_id JOIN job_steps s ON s.id=NEW.step_id
 WHERE d.id=NEW.draft_id AND d.project_id=NEW.project_id AND r.revision=NEW.revision
 AND j.project_id=NEW.project_id AND j.operation='publication.send'
 AND s.job_id=j.id AND s.side_effect_class='external_submit' AND s.target=NEW.target
 AND json_extract(NEW.target,'$.project_id')=NEW.project_id
 AND json_extract(NEW.target,'$.draft_id')=NEW.draft_id
 AND json_extract(NEW.target,'$.revision')=NEW.revision
 AND json_extract(NEW.target,'$.draft_digest')=r.digest
 AND json_extract(NEW.target,'$.account')=NEW.account
 AND json_extract(NEW.target,'$.destination')=NEW.destination
 AND json_extract(NEW.target,'$.intent_id')=NEW.id
 AND json_extract(NEW.target,'$.operation')='publication.send'
 AND json_extract(NEW.target,'$.provider')='facebook-pages'
 AND json_type(NEW.target,'$.parent')='null'
 AND json_type(NEW.target,'$.attachments')='array'
 AND json_array_length(NEW.target,'$.attachments')=0
 AND json_extract(NEW.payload,'$.message')=r.text
 AND json_type(NEW.payload,'$.message')='text')
 BEGIN SELECT RAISE(ABORT,'publication ownership mismatch'); END;
CREATE TRIGGER publication_owner_update BEFORE UPDATE OF job_id,step_id ON publications
 WHEN NOT EXISTS(SELECT 1 FROM jobs j JOIN job_steps s ON s.job_id=j.id
 WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND j.operation='publication.send'
 AND s.id=NEW.step_id AND s.target=NEW.target AND s.side_effect_class='external_submit')
 BEGIN SELECT RAISE(ABORT,'publication step ownership mismatch'); END;
CREATE TRIGGER publication_attempt_owner BEFORE INSERT ON publication_attempts
 WHEN NOT EXISTS(SELECT 1 FROM publications p JOIN job_steps s ON s.id=p.step_id
 WHERE p.id=NEW.publication_id AND p.step_id=NEW.step_id AND p.action_digest=NEW.action_digest
 AND p.state='queued' AND s.state='running' AND s.side_effect_class='external_submit')
 BEGIN SELECT RAISE(ABORT,'publication attempt ownership mismatch'); END;
