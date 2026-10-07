-- Gold: publication_trends
-- Grain: publication_year (NULL year is an explicit bucket)
-- Materialization: MATERIALIZATION_CANDIDATE (publication-trends)
-- Partition candidate: publication_year (INTEGER_RANGE alignment)

SELECT
  w.publication_year,
  COUNT(*) AS active_work_count
FROM `openalex.works` AS w
WHERE w.activity_state = 'ACTIVE'
GROUP BY w.publication_year
;
