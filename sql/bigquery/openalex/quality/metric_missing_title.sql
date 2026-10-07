-- INFORMATIONAL: metric.active_works.missing_title
-- Missing = NULL or empty string. Denominator = ACTIVE Works.

SELECT
  COUNTIF(w.`title` IS NULL OR w.`title` = '') AS `missing_count`,
  COUNT(*) AS `active_work_denominator`,
  SAFE_DIVIDE(
    COUNTIF(w.`title` IS NULL OR w.`title` = ''),
    COUNT(*)
  ) AS `missing_rate`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
;
