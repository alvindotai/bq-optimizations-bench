-- Fixtures for the two myths that cannot be tested on public data alone:
--   M6  "denormalise 5-8 tables for sub-second latency"  (needs a wide table to compare against)
--   M7  "cast string keys to INT64 for faster joins"     (needs the same rows keyed both ways)
-- Built in ${PROJECT}.${DATASET} (US, colocated with
-- bigquery-public-data). All rows derive from bigquery-public-data.stackoverflow.

-- ── M7: identical rows, keys typed INT64 vs STRING ──────────────────────────
CREATE OR REPLACE TABLE `${PROJECT}.${DATASET}.k_users_int` AS
SELECT id, reputation FROM `bigquery-public-data.stackoverflow.users`;

CREATE OR REPLACE TABLE `${PROJECT}.${DATASET}.k_users_str` AS
SELECT CAST(id AS STRING) AS id, reputation FROM `bigquery-public-data.stackoverflow.users`;

CREATE OR REPLACE TABLE `${PROJECT}.${DATASET}.k_posts_int` AS
SELECT owner_user_id, view_count, score
FROM `bigquery-public-data.stackoverflow.posts_questions`
WHERE owner_user_id IS NOT NULL;

CREATE OR REPLACE TABLE `${PROJECT}.${DATASET}.k_posts_str` AS
SELECT CAST(owner_user_id AS STRING) AS owner_user_id, view_count, score
FROM `bigquery-public-data.stackoverflow.posts_questions`
WHERE owner_user_id IS NOT NULL;

-- ── M6: a 3-table star, and the same data pre-joined into one wide table ────
CREATE OR REPLACE TABLE `${PROJECT}.${DATASET}.n_posts` AS
SELECT id, owner_user_id, tags, view_count, score, creation_date
FROM `bigquery-public-data.stackoverflow.posts_questions`
WHERE owner_user_id IS NOT NULL;

CREATE OR REPLACE TABLE `${PROJECT}.${DATASET}.n_users` AS
SELECT id, display_name, reputation, location
FROM `bigquery-public-data.stackoverflow.users`;

CREATE OR REPLACE TABLE `${PROJECT}.${DATASET}.n_badges` AS
SELECT user_id, COUNT(*) AS n_badges
FROM `bigquery-public-data.stackoverflow.badges`
GROUP BY user_id;

CREATE OR REPLACE TABLE `${PROJECT}.${DATASET}.denorm_wide` AS
SELECT p.id, p.owner_user_id, p.tags, p.view_count, p.score, p.creation_date,
       u.display_name, u.reputation, u.location,
       IFNULL(b.n_badges, 0) AS n_badges
FROM `${PROJECT}.${DATASET}.n_posts` p
JOIN `${PROJECT}.${DATASET}.n_users` u ON p.owner_user_id = u.id
LEFT JOIN `${PROJECT}.${DATASET}.n_badges` b ON b.user_id = u.id;
