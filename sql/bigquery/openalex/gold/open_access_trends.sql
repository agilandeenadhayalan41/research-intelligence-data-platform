-- Gold: open_access_trends
-- Grain: publication_year + oa_status
-- NULL oa_status / NULL publication_year preserved; do not fabricate 'closed'.
-- Materialization: MATERIALIZATION_CANDIDATE (oa-trends)

SELECT
  w.publication_year,
  w.oa_status,
  COUNT(*) AS active_work_count
FROM `openalex.works` AS w
WHERE w.activity_state = 'ACTIVE'
GROUP BY
  w.publication_year,
  w.oa_status
;
