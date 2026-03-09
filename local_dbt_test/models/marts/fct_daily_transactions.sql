WITH tx AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),
acc AS (
    SELECT * FROM {{ ref('stg_accounts') }}
)
SELECT
    tx.transaction_date,
    acc.account_type,
    COUNT(tx.transaction_id) AS total_transactions,
    SUM(tx.amount) AS total_volume
FROM tx
JOIN acc ON tx.account_id = acc.account_id
WHERE tx.status = 'completed'
GROUP BY 1, 2
