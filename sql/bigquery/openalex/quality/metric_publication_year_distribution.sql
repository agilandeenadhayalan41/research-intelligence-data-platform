-- INFORMATIONAL: metric.active_works.publication_year_distribution
-- NULL publication_year is an explicit unknown bucket (not 0 / 1900 / string).

SELECT
  w.`publication_year`,
  COUNT(*) AS `work_count`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
GROUP BY w.`publication_year`
;
