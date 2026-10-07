-- INFORMATIONAL: metric.active_works.without_authors
-- No usable non-null author_id (not authorships_presence source state).

SELECT
  COUNTIF(a.`work_id` IS NULL) AS `missing_count`,
  COUNT(*) AS `active_work_denominator`,
  SAFE_DIVIDE(COUNTIF(a.`work_id` IS NULL), COUNT(*)) AS `missing_rate`
FROM `openalex.works` AS w
LEFT JOIN (
  SELECT DISTINCT wa.`work_id`
  FROM `openalex.work_authors` AS wa
  WHERE wa.`author_id` IS NOT NULL
) AS a
  ON a.`work_id` = w.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
;
