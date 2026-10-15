-- Cue retirement appends a revision; existing rows remain text-bearing.
-- ADD COLUMN preserves the append-only triggers, indexes and row identities.
ALTER TABLE transcript_segments ADD COLUMN removed INTEGER NOT NULL DEFAULT 0
CHECK(removed IN (0,1) AND (removed=0 OR text=''));
