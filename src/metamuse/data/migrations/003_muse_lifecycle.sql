-- B10 forward migration. No fabricated account/backend or zero-handoff legacy facts.
ALTER TABLE discussions ADD COLUMN account TEXT;
ALTER TABLE discussions ADD COLUMN backend TEXT CHECK(backend IN ('live','fixture'));
ALTER TABLE discussions ADD COLUMN open_request_key TEXT;
ALTER TABLE discussions ADD COLUMN open_payload TEXT CHECK(open_payload IS NULL OR json_valid(open_payload));
ALTER TABLE discussions ADD COLUMN open_step_id TEXT REFERENCES job_steps(id);
ALTER TABLE discussions ADD COLUMN open_permit_id TEXT REFERENCES permits(id);
ALTER TABLE discussions ADD COLUMN open_submitted_at_ms INTEGER CHECK(open_submitted_at_ms IS NULL OR (typeof(open_submitted_at_ms)='integer' AND open_submitted_at_ms>=0));
ALTER TABLE discussions ADD COLUMN open_stream_id INTEGER CHECK(open_stream_id IS NULL OR (typeof(open_stream_id)='integer' AND open_stream_id BETWEEN 1 AND 9223372036854775807));
ALTER TABLE discussions ADD COLUMN open_bytes_handed_off INTEGER CHECK(open_bytes_handed_off IN (0,1));
ALTER TABLE discussions ADD COLUMN open_verdict TEXT CHECK(open_verdict IN ('acked','app_error','undecided'));
ALTER TABLE discussions ADD COLUMN open_error_code TEXT;
ALTER TABLE discussions ADD COLUMN open_delivery_proof TEXT CHECK(open_delivery_proof IS NULL OR json_valid(open_delivery_proof));
ALTER TABLE discussions ADD COLUMN quarantine_step_id TEXT REFERENCES job_steps(id);
CREATE UNIQUE INDEX discussion_open_identity ON discussions(project_id,open_request_key) WHERE open_request_key IS NOT NULL;
ALTER TABLE exchanges ADD COLUMN request_marker TEXT CHECK(request_marker IS NULL OR length(request_marker) BETWEEN 1 AND 128);
ALTER TABLE exchanges ADD COLUMN outbound_payload TEXT CHECK(outbound_payload IS NULL OR json_valid(outbound_payload));
ALTER TABLE exchanges ADD COLUMN outbound_digest TEXT CHECK(outbound_digest IS NULL OR (length(outbound_digest)=64 AND outbound_digest NOT GLOB '*[^0-9a-f]*'));
ALTER TABLE exchanges ADD COLUMN submission_step_id TEXT REFERENCES job_steps(id);
ALTER TABLE exchanges ADD COLUMN submitted_at_ms INTEGER CHECK(submitted_at_ms IS NULL OR (typeof(submitted_at_ms)='integer' AND submitted_at_ms>=0));
ALTER TABLE exchanges ADD COLUMN stream_id INTEGER CHECK(stream_id IS NULL OR (typeof(stream_id)='integer' AND stream_id BETWEEN 1 AND 9223372036854775807));
ALTER TABLE exchanges ADD COLUMN bytes_handed_off INTEGER CHECK(bytes_handed_off IN (0,1));
ALTER TABLE exchanges ADD COLUMN attempt_verdict TEXT CHECK(attempt_verdict IN ('acked','app_error','undecided'));
ALTER TABLE exchanges ADD COLUMN attempt_detail TEXT CHECK(attempt_detail IS NULL OR length(attempt_detail)<=512);
ALTER TABLE exchanges ADD COLUMN attempt_error_code TEXT;
ALTER TABLE exchanges ADD COLUMN delivery_proof TEXT CHECK(delivery_proof IS NULL OR json_valid(delivery_proof));
ALTER TABLE exchanges ADD COLUMN observation_memory TEXT CHECK(observation_memory IS NULL OR json_valid(observation_memory));
CREATE UNIQUE INDEX exchange_marker ON exchanges(request_marker) WHERE request_marker IS NOT NULL;
ALTER TABLE messages ADD COLUMN backend TEXT CHECK(backend IN ('live','fixture'));
ALTER TABLE messages ADD COLUMN identity_key TEXT;
DROP INDEX message_identity;
CREATE UNIQUE INDEX message_identity ON messages(discussion_id,remote_session,remote_message_id) WHERE remote_message_id IS NOT NULL;
CREATE UNIQUE INDEX message_event_identity ON messages(discussion_id,identity_key) WHERE identity_key IS NOT NULL;
CREATE TABLE muse_observations (
 id TEXT PRIMARY KEY, discussion_id TEXT NOT NULL REFERENCES discussions(id), message_id TEXT,
 identity_key TEXT NOT NULL CHECK(length(identity_key)<=1024), raw_event TEXT NOT NULL CHECK(json_valid(raw_event) AND length(CAST(raw_event AS BLOB))<=65536),
 observation_digest TEXT NOT NULL CHECK(length(observation_digest)=64 AND observation_digest NOT GLOB '*[^0-9a-f]*'),
 observed_at TEXT NOT NULL, backend TEXT NOT NULL CHECK(backend IN ('live','fixture')),
 FOREIGN KEY(message_id,discussion_id) REFERENCES messages(id,discussion_id),
 UNIQUE(discussion_id,identity_key,observation_digest)
);
CREATE UNIQUE INDEX message_discussion_fk ON messages(id,discussion_id);
CREATE TRIGGER muse_observation_no_replace BEFORE INSERT ON muse_observations
WHEN EXISTS(SELECT 1 FROM muse_observations WHERE rowid=NEW.rowid OR id=NEW.id OR (discussion_id=NEW.discussion_id AND identity_key=NEW.identity_key AND observation_digest=NEW.observation_digest))
BEGIN SELECT RAISE(ABORT,'observation identity retained'); END;
CREATE TRIGGER muse_observation_immutable BEFORE UPDATE ON muse_observations BEGIN SELECT RAISE(ABORT,'append-only observation'); END;
CREATE TRIGGER muse_observation_identity_immutable BEFORE UPDATE ON muse_observations
WHEN NEW.rowid IS NOT OLD.rowid OR NEW.id IS NOT OLD.id OR NEW.discussion_id IS NOT OLD.discussion_id OR NEW.message_id IS NOT OLD.message_id OR NEW.identity_key IS NOT OLD.identity_key OR NEW.observation_digest IS NOT OLD.observation_digest OR NEW.observed_at IS NOT OLD.observed_at OR NEW.backend IS NOT OLD.backend
BEGIN SELECT RAISE(ABORT,'observation identity is immutable'); END;
CREATE TRIGGER muse_observation_retained BEFORE DELETE ON muse_observations BEGIN SELECT RAISE(ABORT,'observation retained'); END;
CREATE TRIGGER muse_observation_backend BEFORE INSERT ON muse_observations
WHEN NEW.backend IS NOT (SELECT backend FROM discussions WHERE id=NEW.discussion_id)
BEGIN SELECT RAISE(ABORT,'observation backend mismatch'); END;
CREATE TRIGGER discussion_no_replace BEFORE INSERT ON discussions
WHEN EXISTS(SELECT 1 FROM discussions WHERE rowid=NEW.rowid OR id=NEW.id OR (project_id=NEW.project_id AND open_request_key=NEW.open_request_key))
BEGIN SELECT RAISE(ABORT,'discussion identity retained'); END;
CREATE TRIGGER discussion_retained BEFORE DELETE ON discussions BEGIN SELECT RAISE(ABORT,'discussion retained'); END;
CREATE TRIGGER discussion_identity_immutable BEFORE UPDATE ON discussions
WHEN NEW.rowid IS NOT OLD.rowid OR NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id OR NEW.provider IS NOT OLD.provider OR NEW.auth_ref IS NOT OLD.auth_ref OR NEW.account IS NOT OLD.account OR NEW.backend IS NOT OLD.backend OR NEW.topic IS NOT OLD.topic OR NEW.open_request_key IS NOT OLD.open_request_key OR NEW.open_payload IS NOT OLD.open_payload OR NEW.open_permit_id IS NOT OLD.open_permit_id
BEGIN SELECT RAISE(ABORT,'discussion binding and intent immutable'); END;
CREATE TRIGGER discussion_attempt_once BEFORE UPDATE ON discussions
WHEN (OLD.open_step_id IS NOT NULL AND NEW.open_step_id IS NOT OLD.open_step_id)
 OR (OLD.open_submitted_at_ms IS NOT NULL AND NEW.open_submitted_at_ms IS NOT OLD.open_submitted_at_ms)
 OR (OLD.open_stream_id IS NOT NULL AND NEW.open_stream_id IS NOT OLD.open_stream_id)
 OR (OLD.open_bytes_handed_off IS NOT NULL AND NEW.open_bytes_handed_off IS NOT OLD.open_bytes_handed_off)
 OR (OLD.open_verdict IS NOT NULL AND NEW.open_verdict IS NOT OLD.open_verdict)
 OR (OLD.open_error_code IS NOT NULL AND NEW.open_error_code IS NOT OLD.open_error_code)
 OR (OLD.open_delivery_proof IS NOT NULL AND NEW.open_delivery_proof IS NOT OLD.open_delivery_proof)
 OR (OLD.remote_session IS NOT NULL AND NEW.remote_session IS NOT OLD.remote_session)
 OR NEW.last_seq<OLD.last_seq
BEGIN SELECT RAISE(ABORT,'discussion attempt is write once'); END;
CREATE TRIGGER discussion_step_guard BEFORE UPDATE OF open_step_id ON discussions
WHEN NEW.open_step_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM job_steps s JOIN jobs j ON j.id=s.job_id WHERE s.id=NEW.open_step_id AND j.project_id=NEW.project_id AND j.operation='muse.open' AND s.side_effect_class='external_submit' AND json_extract(s.target,'$.intent_id')=NEW.id)
BEGIN SELECT RAISE(ABORT,'original open step required'); END;
CREATE TRIGGER discussion_quarantine_guard BEFORE UPDATE OF quarantine_step_id ON discussions
WHEN (NEW.quarantine_step_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM job_steps s JOIN jobs j ON j.id=s.job_id WHERE s.id=NEW.quarantine_step_id AND j.project_id=NEW.project_id AND s.state IN ('submitting','open_uncertain','send_uncertain','needs_reconcile')))
 OR (OLD.quarantine_step_id IS NOT NULL AND NEW.quarantine_step_id IS NULL AND EXISTS(SELECT 1 FROM job_steps WHERE id=OLD.quarantine_step_id AND state IN ('submitting','open_uncertain','send_uncertain','needs_reconcile')))
BEGIN SELECT RAISE(ABORT,'unresolved attempt quarantined'); END;
CREATE TRIGGER message_no_replace BEFORE INSERT ON messages
WHEN EXISTS(SELECT 1 FROM messages WHERE rowid=NEW.rowid OR id=NEW.id OR (discussion_id=NEW.discussion_id AND (identity_key=NEW.identity_key OR (remote_session=NEW.remote_session AND remote_message_id=NEW.remote_message_id))))
BEGIN SELECT RAISE(ABORT,'message identity retained'); END;
CREATE TRIGGER message_identity_immutable BEFORE UPDATE ON messages
WHEN NEW.rowid IS NOT OLD.rowid OR NEW.id IS NOT OLD.id OR NEW.discussion_id IS NOT OLD.discussion_id OR NEW.remote_session IS NOT OLD.remote_session OR NEW.remote_message_id IS NOT OLD.remote_message_id OR NEW.seq IS NOT OLD.seq OR NEW.role IS NOT OLD.role OR NEW.backend IS NOT OLD.backend OR NEW.identity_key IS NOT OLD.identity_key OR NEW.observed_at IS NOT OLD.observed_at
BEGIN SELECT RAISE(ABORT,'message identity immutable'); END;
CREATE TRIGGER message_exchange_once BEFORE UPDATE OF exchange_id ON messages
WHEN OLD.exchange_id IS NOT NULL AND NEW.exchange_id IS NOT OLD.exchange_id
BEGIN SELECT RAISE(ABORT,'message association retained'); END;
CREATE TRIGGER message_backend_guard BEFORE INSERT ON messages
WHEN NEW.backend IS NOT (SELECT backend FROM discussions WHERE id=NEW.discussion_id)
BEGIN SELECT RAISE(ABORT,'message backend mismatch'); END;
CREATE TRIGGER message_retained BEFORE DELETE ON messages BEGIN SELECT RAISE(ABORT,'message retained'); END;
CREATE TRIGGER exchange_retained BEFORE DELETE ON exchanges BEGIN SELECT RAISE(ABORT,'exchange retained'); END;
DROP TRIGGER exchanges_identity_immutable;
CREATE TRIGGER exchanges_identity_immutable BEFORE UPDATE ON exchanges
WHEN NEW.rowid IS NOT OLD.rowid OR NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id OR NEW.discussion_id IS NOT OLD.discussion_id OR NEW.request_key IS NOT OLD.request_key OR NEW.question IS NOT OLD.question OR NEW.packet_id IS NOT OLD.packet_id OR NEW.digest IS NOT OLD.digest OR NEW.permit_id IS NOT OLD.permit_id OR NEW.baseline IS NOT OLD.baseline OR NEW.request_marker IS NOT OLD.request_marker OR NEW.outbound_payload IS NOT OLD.outbound_payload OR NEW.outbound_digest IS NOT OLD.outbound_digest
BEGIN SELECT RAISE(ABORT,'exchange identity is immutable'); END;
CREATE TRIGGER exchange_attempt_once BEFORE UPDATE ON exchanges
WHEN (OLD.job_id IS NOT NULL AND NEW.job_id IS NOT OLD.job_id)
 OR (OLD.submission_step_id IS NOT NULL AND NEW.submission_step_id IS NOT OLD.submission_step_id)
 OR (OLD.submitted_at_ms IS NOT NULL AND NEW.submitted_at_ms IS NOT OLD.submitted_at_ms)
 OR (OLD.stream_id IS NOT NULL AND NEW.stream_id IS NOT OLD.stream_id)
 OR (OLD.bytes_handed_off IS NOT NULL AND NEW.bytes_handed_off IS NOT OLD.bytes_handed_off)
 OR (OLD.attempt_verdict IS NOT NULL AND NEW.attempt_verdict IS NOT OLD.attempt_verdict)
 OR (OLD.attempt_detail IS NOT NULL AND NEW.attempt_detail IS NOT OLD.attempt_detail)
 OR (OLD.attempt_error_code IS NOT NULL AND NEW.attempt_error_code IS NOT OLD.attempt_error_code)
 OR (OLD.delivery_proof IS NOT NULL AND NEW.delivery_proof IS NOT OLD.delivery_proof)
BEGIN SELECT RAISE(ABORT,'exchange attempt is write once'); END;
UPDATE exchanges SET state='closed' WHERE outcome IS NOT NULL;
UPDATE exchanges SET state='send_uncertain' WHERE outcome IS NULL AND state NOT IN ('prepared','submitting','sent','send_uncertain','reply_observed');
CREATE TRIGGER exchange_state_insert BEFORE INSERT ON exchanges
WHEN NEW.state NOT IN ('prepared','submitting','sent','send_uncertain','failed_before_send','reply_observed','closed') OR ((NEW.state='closed')<>(NEW.outcome IS NOT NULL)) OR (NEW.state='failed_before_send' AND NEW.bytes_handed_off IS NOT 0)
BEGIN SELECT RAISE(ABORT,'invalid exchange state'); END;
CREATE TRIGGER exchange_state_guard BEFORE UPDATE ON exchanges
WHEN NEW.state NOT IN ('prepared','submitting','sent','send_uncertain','failed_before_send','reply_observed','closed')
 OR ((NEW.state='closed')<>(NEW.outcome IS NOT NULL))
 OR (OLD.state='closed' AND NEW.state<>'closed')
 OR (NEW.state<>OLD.state AND NOT (
 (OLD.state='prepared' AND NEW.state IN ('submitting','closed')) OR
 (OLD.state='submitting' AND NEW.state IN ('sent','send_uncertain','failed_before_send','closed')) OR
 (OLD.state='send_uncertain' AND NEW.state IN ('sent','reply_observed','failed_before_send','closed')) OR
 (OLD.state='sent' AND NEW.state IN ('reply_observed','closed')) OR
 (OLD.state IN ('reply_observed','failed_before_send') AND NEW.state='closed')))
 OR (NEW.state='failed_before_send' AND (NEW.bytes_handed_off IS NOT 0 OR json_extract(NEW.delivery_proof,'$.kind') IS NOT 'not_submitted'))
BEGIN SELECT RAISE(ABORT,'invalid exchange transition'); END;
CREATE TRIGGER exchange_submit_guard BEFORE UPDATE ON exchanges
WHEN NEW.state='submitting' AND (NEW.request_marker IS NULL OR NEW.outbound_payload IS NULL OR NEW.outbound_digest IS NULL OR NEW.submitted_at_ms IS NULL OR NEW.submission_step_id IS NULL OR NOT EXISTS(SELECT 1 FROM job_steps s JOIN jobs j ON j.id=s.job_id WHERE s.id=NEW.submission_step_id AND j.id=NEW.job_id AND j.project_id=NEW.project_id AND j.operation='muse.send' AND s.side_effect_class='external_submit' AND json_extract(s.target,'$.intent_id')=NEW.id))
BEGIN SELECT RAISE(ABORT,'prepared original attempt required'); END;
CREATE UNIQUE INDEX exchange_original_step ON exchanges(submission_step_id) WHERE submission_step_id IS NOT NULL;
DROP TRIGGER exchange_no_replace;
CREATE TRIGGER exchange_no_replace BEFORE INSERT ON exchanges
WHEN EXISTS(SELECT 1 FROM exchanges WHERE rowid=NEW.rowid OR id=NEW.id OR (discussion_id=NEW.discussion_id AND (request_key=NEW.request_key OR (outcome IS NULL AND NEW.outcome IS NULL))) OR request_marker=NEW.request_marker OR submission_step_id=NEW.submission_step_id)
BEGIN SELECT RAISE(ABORT,'exchange replacement is forbidden'); END;
CREATE TRIGGER exchange_prepared_insert BEFORE INSERT ON exchanges
WHEN NEW.request_marker IS NOT NULL AND (NEW.outbound_payload IS NULL OR NEW.outbound_digest IS NULL OR NEW.submission_step_id IS NULL OR NEW.state<>'prepared' OR NOT EXISTS(SELECT 1 FROM job_steps s JOIN jobs j ON j.id=s.job_id WHERE s.id=NEW.submission_step_id AND j.id=NEW.job_id AND j.project_id=NEW.project_id AND j.operation='muse.send' AND s.side_effect_class='external_submit' AND json_extract(s.target,'$.intent_id')=NEW.id))
BEGIN SELECT RAISE(ABORT,'new exchange must bind its prepared original step'); END;
CREATE TRIGGER exchange_delivery_proof BEFORE UPDATE OF delivery_proof ON exchanges
WHEN NEW.delivery_proof IS NOT NULL AND (json_type(NEW.delivery_proof) IS NOT 'object' OR json_extract(NEW.delivery_proof,'$.step_id') IS NOT NEW.submission_step_id OR json_extract(NEW.delivery_proof,'$.outbound_digest') IS NOT NEW.outbound_digest OR json_extract(NEW.delivery_proof,'$.kind') IS NULL OR json_extract(NEW.delivery_proof,'$.kind') NOT IN ('ack','user_echo','not_submitted')
 OR (json_extract(NEW.delivery_proof,'$.kind')='ack' AND (NEW.attempt_verdict IS NOT 'acked' OR NEW.bytes_handed_off IS NOT 1))
 OR (json_extract(NEW.delivery_proof,'$.kind')='not_submitted' AND NEW.bytes_handed_off IS NOT 0)
 OR (json_extract(NEW.delivery_proof,'$.kind')='user_echo' AND NOT EXISTS(SELECT 1 FROM muse_observations o JOIN discussions d ON d.id=o.discussion_id WHERE o.id=json_extract(NEW.delivery_proof,'$.observation_id') AND o.discussion_id=NEW.discussion_id AND json_extract(o.raw_event,'$.event_name')='message.user' AND json_extract(o.raw_event,'$.session_id')=d.remote_session AND json_extract(o.raw_event,'$.seq')>json_extract(NEW.baseline,'$.seq'))))
BEGIN SELECT RAISE(ABORT,'delivery proof must reference original attempt evidence'); END;
CREATE TRIGGER exchange_memory_guard BEFORE UPDATE OF observation_memory ON exchanges
WHEN NEW.observation_memory IS NOT NULL AND (json_extract(NEW.observation_memory,'$.version') IS NOT 1 OR json_type(NEW.observation_memory,'$.ambiguous') IS NULL OR json_type(NEW.observation_memory,'$.ambiguous') NOT IN ('true','false') OR json_type(NEW.observation_memory,'$.competing') IS NOT 'array' OR json_type(NEW.observation_memory,'$.disqualified') IS NOT 'array' OR json_type(NEW.observation_memory,'$.high_water') IS NOT 'integer' OR json_extract(NEW.observation_memory,'$.high_water')<0)
BEGIN SELECT RAISE(ABORT,'versioned association memory required'); END;
CREATE TRIGGER discussion_mapping_guard BEFORE UPDATE OF remote_session,open_delivery_proof ON discussions
WHEN NEW.remote_session IS NOT OLD.remote_session AND NEW.remote_session IS NOT NULL AND (NEW.open_delivery_proof IS NULL OR json_extract(NEW.open_delivery_proof,'$.kind') IS NULL OR json_extract(NEW.open_delivery_proof,'$.kind') NOT IN ('ack','session_marker') OR json_extract(NEW.open_delivery_proof,'$.step_id') IS NOT NEW.open_step_id OR NOT (
 (json_extract(NEW.open_delivery_proof,'$.kind')='ack' AND NEW.open_verdict='acked' AND NEW.open_bytes_handed_off=1) OR
 (json_extract(NEW.open_delivery_proof,'$.kind')='session_marker' AND EXISTS(SELECT 1 FROM muse_observations o WHERE o.discussion_id=NEW.id AND json_extract(o.raw_event,'$.session_id')=NEW.remote_session AND json_extract(o.raw_event,'$.request_marker')=json_extract(NEW.open_payload,'$.marker')))))
BEGIN SELECT RAISE(ABORT,'original session mapping proof required'); END;
CREATE UNIQUE INDEX discussion_remote_identity ON discussions(provider,account,backend,remote_session)
WHERE account IS NOT NULL AND backend IS NOT NULL AND remote_session IS NOT NULL;
DROP TRIGGER discussion_no_replace;
CREATE TRIGGER discussion_no_replace BEFORE INSERT ON discussions
WHEN EXISTS(SELECT 1 FROM discussions WHERE rowid=NEW.rowid OR id=NEW.id OR (project_id=NEW.project_id AND open_request_key=NEW.open_request_key) OR (provider=NEW.provider AND account=NEW.account AND backend=NEW.backend AND remote_session=NEW.remote_session))
BEGIN SELECT RAISE(ABORT,'discussion identity retained'); END;
CREATE TRIGGER exchange_zero_proof_insert BEFORE INSERT ON exchanges
WHEN NEW.state='failed_before_send' AND (NEW.bytes_handed_off IS NOT 0 OR json_extract(NEW.delivery_proof,'$.kind') IS NOT 'not_submitted')
BEGIN SELECT RAISE(ABORT,'affirmative zero handoff proof required'); END;
