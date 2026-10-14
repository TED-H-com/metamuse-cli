-- AT-042 reuses the retained, append-only v3 observation identity. No new columns.
CREATE TRIGGER draft_muse_evidence_guard BEFORE INSERT ON draft_revisions
WHEN EXISTS (
 SELECT 1 FROM json_each(NEW.facts_refs) ref
 WHERE EXISTS (
  SELECT 1 FROM json_each(ref.value) field
  WHERE field.key IN ('kind','id','revision') GROUP BY field.key HAVING count(*)>1
 ) OR (json_extract(ref.value,'$.kind')='muse_observation' AND (
  json_type(ref.value,'$.revision') IS NOT 'integer' OR
  json_extract(ref.value,'$.revision') IS NOT 1 OR NOT EXISTS (
   SELECT 1 FROM muse_observations o JOIN discussions d ON d.id=o.discussion_id
   JOIN drafts draft ON draft.id=NEW.draft_id
   WHERE o.id=json_extract(ref.value,'$.id') AND d.project_id=draft.project_id
  )
 ))
)
BEGIN SELECT RAISE(ABORT,'draft Muse evidence must exist in project at revision 1'); END;
CREATE TRIGGER packet_muse_evidence_guard BEFORE INSERT ON packets
WHEN EXISTS (
 SELECT 1 FROM json_each(NEW.selection) ref
 WHERE json_extract(ref.value,'$.kind')='muse_observation' OR EXISTS (
  SELECT 1 FROM json_each(ref.value) field
  WHERE field.key IN ('kind','id','revision') GROUP BY field.key HAVING count(*)>1
 )
)
BEGIN SELECT RAISE(ABORT,'Muse observations cannot be re-shared in packets'); END;
