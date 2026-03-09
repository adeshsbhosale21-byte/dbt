WITH raw AS (
    SELECT * FROM {{ ref('raw_transactions') }}
)
SELECT
    transaction_id,
    account_id,
    amount,
    currency,
    status,
    CAST(transaction_date AS DATE) AS transaction_date,
    merchant_name
FROM raw
