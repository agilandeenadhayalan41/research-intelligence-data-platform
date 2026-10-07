-- Gold: research_discovery
-- Grain: one row per ACTIVE work_id
-- Fan-out safety: independent author/topic/institution aggregates JOIN at Work grain.
-- Materialization: COMPUTE_ON_READ (no MEASURED BigQuery evidence)

WITH active_works AS (
  SELECT
    w.work_id,
    w.doi,
    w.title,
    w.publication_year,
    w.publication_date,
    w.work_type,
    w.language,
    w.is_oa,
    w.oa_status,
    w.primary_source_id,
    w.primary_publisher_id
  FROM `openalex.works` AS w
  WHERE w.activity_state = 'ACTIVE'
),
authors_agg AS (
  SELECT
    wa.work_id,
    ARRAY_AGG(DISTINCT wa.author_id IGNORE NULLS ORDER BY wa.author_id) AS author_ids
  FROM `openalex.work_authors` AS wa
  INNER JOIN active_works AS aw
    ON aw.work_id = wa.work_id
  GROUP BY wa.work_id
),
topics_agg AS (
  SELECT
    wt.work_id,
    ARRAY_AGG(DISTINCT wt.topic_id IGNORE NULLS ORDER BY wt.topic_id) AS topic_ids
  FROM `openalex.work_topics` AS wt
  INNER JOIN active_works AS aw
    ON aw.work_id = wt.work_id
  GROUP BY wt.work_id
),
institutions_agg AS (
  SELECT
    wai.work_id,
    ARRAY_AGG(DISTINCT wai.institution_id IGNORE NULLS ORDER BY wai.institution_id)
      AS institution_ids
  FROM `openalex.work_author_institutions` AS wai
  INNER JOIN active_works AS aw
    ON aw.work_id = wai.work_id
  GROUP BY wai.work_id
)
SELECT
  aw.work_id,
  aw.doi,
  aw.title,
  aw.publication_year,
  aw.publication_date,
  aw.work_type,
  aw.language,
  aw.is_oa,
  aw.oa_status,
  aw.primary_source_id,
  aw.primary_publisher_id,
  aa.author_ids,
  ta.topic_ids,
  ia.institution_ids
FROM active_works AS aw
LEFT JOIN authors_agg AS aa
  ON aa.work_id = aw.work_id
LEFT JOIN topics_agg AS ta
  ON ta.work_id = aw.work_id
LEFT JOIN institutions_agg AS ia
  ON ia.work_id = aw.work_id
;
