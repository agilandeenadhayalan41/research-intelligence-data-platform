-- pattern_id: open-access-trends
-- BigQuery Standard SQL — Step 15 / #22 (NOT YET DEPLOYED)
-- Partition key is publication_year (INTEGER_RANGE). @year_from/@year_to
-- filter that column for pruning. Explicit columns only.

SELECT
  w.`publication_year`,
  w.`oa_status`,
  COUNT(*) AS `work_count`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
  AND (@year_from IS NULL OR w.`publication_year` >= @year_from)
  AND (@year_to IS NULL OR w.`publication_year` <= @year_to)
GROUP BY w.`publication_year`, w.`oa_status`
ORDER BY w.`publication_year`, w.`oa_status`;
