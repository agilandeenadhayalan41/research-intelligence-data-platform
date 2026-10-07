-- INFORMATIONAL: metric.active_works.missing_doi
-- Denominator = ACTIVE Works. rate NULL when denominator = 0.

SELECT
  COUNTIF(w.`doi` IS NULL) AS `missing_count`,
  COUNT(*) AS `active_work_denominator`,
  SAFE_DIVIDE(COUNTIF(w.`doi` IS NULL), COUNT(*)) AS `missing_rate`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
;
