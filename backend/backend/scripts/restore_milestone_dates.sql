-- Restore the dates of the shared milestones copied from projects.data['milestones']
--
-- The migration a4c2e8f1b7d3 copied each shared milestone to the milestones table, with data->>'legacy_key'
-- = its key in projects.data['milestones'], and kept projects.data['milestones'] untouched. Each legacy
-- milestone has its date ("date", an ISO string saved by browsers): we put it back in created_at and updated_at.
--
-- Run it with:  psql -v ON_ERROR_STOP=1 -f restore_milestone_dates.sql
-- It ROLLS BACK by default: read the output, then re-run with  -v apply=1  to COMMIT.
--
-- Not covered: personal milestones imported from browsers (POST /api/v1/milestones/import). Their old copy was
-- in localStorage, which the web app clears after the import: there's no server-side backup of their dates.

\set ON_ERROR_STOP 1
\if :{?apply}
\else
  \set apply 0
\endif

BEGIN;
-- Legacy dates without a timezone are read as UTC, like the migration did
SET LOCAL TimeZone = 'UTC';

-- 0. Keep the current dates, to undo this script:
--    UPDATE milestones m SET created_at = b.created_at, updated_at = b.updated_at FROM milestones_dates_before_restore b WHERE b.id = m.id;
CREATE TABLE IF NOT EXISTS milestones_dates_before_restore AS
  SELECT id, created_at, updated_at, now() AS saved_at FROM milestones;

-- 1. What we'd do, one row per milestone that has a legacy copy
CREATE TEMP TABLE restore_plan ON COMMIT DROP AS
WITH legacy AS (
  SELECT p.id AS project_id, l.key, l.value AS legacy
  FROM projects p, jsonb_each(CAST(p.data AS jsonb)->'milestones') l
  WHERE jsonb_typeof(CAST(p.data AS jsonb)->'milestones') = 'object'
    AND jsonb_typeof(l.value) = 'object'
)
SELECT
  m.id,
  m.project_id,
  m.label,
  legacy.key AS legacy_key,
  legacy.legacy->>'date' AS legacy_date_raw,
  CASE WHEN legacy.legacy->>'date' ~ '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}'
       THEN (legacy.legacy->>'date')::timestamptz AT TIME ZONE 'UTC' END AS legacy_date,
  m.created_at,
  m.updated_at,
  -- edited since the migration (other label/notes/results): keep updated_at, only restore created_at
  (m.label   IS DISTINCT FROM left(coalesce(legacy.legacy->>'label', ''), 200)
   OR m.notes IS DISTINCT FROM left(coalesce(legacy.legacy->>'notes', ''), 20000)
   OR m.commit_hexsha IS DISTINCT FROM legacy.legacy->>'commit'
   OR m.batch IS DISTINCT FROM coalesce(nullif(legacy.legacy->>'batch', ''), 'default')
   OR m.filter IS DISTINCT FROM coalesce(legacy.legacy->>'filter', '')
  ) AS edited_since,
  count(*) OVER (PARTITION BY m.project_id, legacy.key) AS copies
FROM milestones m
JOIN legacy ON legacy.project_id = m.project_id AND legacy.key = m.data->>'legacy_key'
WHERE m.shared;

-- 2. Check: the summary. Expect: no_date and duplicate_copies at 0 (or understood), to_fix > 0
SELECT
  count(*)                                                           AS milestones_with_legacy_copy,
  count(*) FILTER (WHERE legacy_date IS NULL)                        AS no_date_in_legacy_copy,
  count(*) FILTER (WHERE copies > 1)                                 AS duplicate_copies,
  count(*) FILTER (WHERE legacy_date IS NOT NULL AND created_at IS DISTINCT FROM legacy_date) AS to_fix,
  count(*) FILTER (WHERE legacy_date IS NOT NULL AND created_at = legacy_date)                AS already_right,
  count(*) FILTER (WHERE edited_since)                               AS edited_since_keep_updated_at,
  (SELECT count(*) FROM milestones WHERE shared AND data ? 'legacy_key')  AS shared_legacy_milestones_total
FROM restore_plan;

-- 3. Check: every change, oldest first. Spot-check a few against what you remember / the web app
SELECT id, project_id, label, legacy_date_raw,
       created_at AS created_at_now, legacy_date AS created_at_after,
       updated_at AS updated_at_now, CASE WHEN edited_since THEN updated_at ELSE legacy_date END AS updated_at_after,
       edited_since, copies
FROM restore_plan
WHERE legacy_date IS NOT NULL AND (created_at IS DISTINCT FROM legacy_date OR (NOT edited_since AND updated_at IS DISTINCT FROM legacy_date))
ORDER BY legacy_date;

-- 4. The update
UPDATE milestones m
SET created_at = r.legacy_date,
    updated_at = CASE WHEN r.edited_since THEN m.updated_at ELSE r.legacy_date END
FROM restore_plan r
WHERE r.id = m.id AND r.legacy_date IS NOT NULL;

-- 5. Check: after the update, should be 0
SELECT count(*) AS still_wrong
FROM restore_plan r JOIN milestones m ON m.id = r.id
WHERE r.legacy_date IS NOT NULL AND m.created_at IS DISTINCT FROM r.legacy_date;

\if :apply
  COMMIT;
  \echo 'COMMITTED. To undo: see step 0 at the top of the script.'
\else
  ROLLBACK;
  \echo 'ROLLED BACK (dry run): nothing changed. Re-run with -v apply=1 to commit.'
\endif
