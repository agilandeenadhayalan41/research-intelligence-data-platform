-- INFORMATIONAL: metric.active_works.work_type_distribution
-- NULL work_type is an explicit unknown bucket.

SELECT
  w.`work_type`,
  COUNT(*) AS `work_count`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
GROUP BY w.`work_type`
;
