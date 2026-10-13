-- Additive collect/media schema (B05/B06).
-- transcript_segments: project scope, optional asset, item/source anchor,
-- cue order and overlap. Existing rows copy only when the asset links to
-- exactly one project; ambiguous or unlinked transcripts abort the upgrade.
-- source_assets: project-scoped source relation for a media file that has
-- no post or comment item. Migration 001 is unchanged.

CREATE TABLE transcript_segments_v2 (
 id TEXT NOT NULL,
 project_id TEXT NOT NULL REFERENCES projects(id),
 asset_id TEXT REFERENCES assets(id),
 item_id TEXT,
 source_id TEXT,
 revision INTEGER NOT NULL CHECK(revision>=1),
 cue_index INTEGER NOT NULL CHECK(cue_index>=0),
 start_ms INTEGER NOT NULL,
 end_ms INTEGER NOT NULL,
 text TEXT NOT NULL,
 language TEXT NOT NULL,
 method TEXT NOT NULL CHECK(method IN ('provided','auto','asr','manual')),
 review TEXT NOT NULL,
 overlap INTEGER NOT NULL CHECK(overlap IN (0,1)),
 PRIMARY KEY(id, revision),
 CHECK(end_ms>=start_ms AND start_ms>=0),
 CHECK(asset_id IS NOT NULL OR item_id IS NOT NULL OR source_id IS NOT NULL),
 FOREIGN KEY(item_id, project_id) REFERENCES items(id, project_id),
 FOREIGN KEY(source_id, project_id) REFERENCES sources(id, project_id)
);

INSERT INTO transcript_segments_v2 (
 id, project_id, asset_id, item_id, source_id, revision, cue_index,
 start_ms, end_ms, text, language, method, review, overlap
)
SELECT
 t.id,
 (SELECT MIN(i.project_id) FROM item_assets ia
  JOIN items i ON i.id=ia.item_id WHERE ia.asset_id=t.asset_id),
 t.asset_id, NULL, NULL, t.revision, 0,
 t.start_ms, t.end_ms, t.text, t.language, t.method, t.review, 0
FROM transcript_segments t
WHERE (
 SELECT COUNT(DISTINCT i.project_id) FROM item_assets ia
 JOIN items i ON i.id=ia.item_id WHERE ia.asset_id=t.asset_id
)=1;

-- Zero rows succeeds. Any unscoped or multi-project transcript inserts NULL
-- project_id, fails NOT NULL, and rolls the upgrade back with data retained.
INSERT INTO transcript_segments_v2 (
 id, project_id, asset_id, revision, cue_index, start_ms, end_ms, text,
 language, method, review, overlap
)
SELECT
 t.id, NULL, t.asset_id, t.revision, 0, t.start_ms, t.end_ms, t.text,
 t.language, t.method, t.review, 0
FROM transcript_segments t
WHERE (
 SELECT COUNT(DISTINCT i.project_id) FROM item_assets ia
 JOIN items i ON i.id=ia.item_id WHERE ia.asset_id=t.asset_id
)<>1;

DROP TABLE transcript_segments;
ALTER TABLE transcript_segments_v2 RENAME TO transcript_segments;
-- 001 defines no transcript trigger or secondary index. The rebuild must
-- still leave (id, revision) append-only, matching item_revisions.
CREATE TRIGGER transcript_revision_immutable BEFORE UPDATE ON transcript_segments
BEGIN SELECT RAISE(ABORT,'append-only revision'); END;
CREATE INDEX transcript_project_item ON transcript_segments(project_id, item_id);
CREATE INDEX transcript_asset ON transcript_segments(asset_id);

CREATE TABLE source_assets (
 source_id TEXT NOT NULL,
 project_id TEXT NOT NULL,
 asset_id TEXT NOT NULL REFERENCES assets(id),
 relation TEXT NOT NULL,
 PRIMARY KEY(source_id, asset_id, relation),
 FOREIGN KEY(source_id, project_id) REFERENCES sources(id, project_id)
);
CREATE INDEX source_assets_project ON source_assets(project_id, asset_id);
