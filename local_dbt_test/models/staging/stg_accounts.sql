WITH raw AS (
    SELECT * FROM {{ ref('raw_accounts') }}
)
SELECT
    account_id,
    customer_name,
    account_type,
    CAST(created_at AS DATE) AS created_at
FROM raw
