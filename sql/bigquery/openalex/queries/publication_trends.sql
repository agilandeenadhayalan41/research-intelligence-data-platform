-- pattern_id: publication-trends
-- BigQuery Standard SQL — Step 15 / #22 (NOT YET DEPLOYED)
-- Partition key is publication_year (INTEGER_RANGE). @year_from/@year_to
-- filter that column for pruning; do not substitute publication_date.

SELECT
  w.`publication_year`,
  COUNT(*) AS `work_count`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
  AND (@year_from IS NULL OR w.`publication_year` >= @year_from)
  AND (@year_to IS NULL OR w.`publication_year` <= @year_to)
GROUP BY w.`publication_year`
ORDER BY w.`publication_year`;
