-- Seed only the customers and accounts referenced by docs/payload-examples/events/*.json.
--
-- Intended use, from the repository root:
--   docker compose exec -T postgres psql -U orchestrator -d outreach_orchestrator < docs/payload-examples/others/seed-phase-4-customers-accounts.sql
--
-- This script does not insert inbound events, audit rows, policy decisions, or outreach tasks.
-- It only creates/upserts the customer and account rows required for manually testing
-- the Phase 4 event payload examples.

BEGIN;

WITH seed_customers (
    id,
    external_id,
    full_name,
    timezone,
    phone_number,
    email,
    sms_consent,
    call_consent,
    email_consent,
    opted_out
) AS (
    VALUES
        (
            '00000000-0000-4000-8000-000000000001'::uuid,
            'cust_jane_doe_001',
            'Jane Doe',
            'America/New_York',
            '+141****0100',
            'jane.doe@example.com',
            true,
            true,
            true,
            false
        ),
        (
            '00000000-0000-4000-8000-000000000002'::uuid,
            'cust_robert_smith_001',
            'Robert Smith',
            'America/Chicago',
            '+131****0100',
            'robert.smith@example.com',
            true,
            false,
            true,
            false
        ),
        (
            '00000000-0000-4000-8000-000000000003'::uuid,
            'cust_maria_santos_001',
            'Maria Santos',
            'America/New_York',
            '+164****0100',
            'maria.santos@example.com',
            true,
            true,
            true,
            false
        ),
        (
            '00000000-0000-4000-8000-000000000004'::uuid,
            'cust_sam_taylor_001',
            'Sam Taylor',
            'America/New_York',
            '+121****0100',
            'sam.taylor@example.com',
            false,
            false,
            false,
            true
        ),
        (
            '00000000-0000-4000-8000-000000000005'::uuid,
            'cust_alex_johnson_001',
            'Alex Johnson',
            'America/Los_Angeles',
            '+141****0101',
            'alex.johnson@example.com',
            true,
            true,
            true,
            false
        ),
        (
            '00000000-0000-4000-8000-000000000006'::uuid,
            'cust_taylor_lee_001',
            'Taylor Lee',
            'America/Denver',
            '+130****0100',
            'taylor.lee@example.com',
            true,
            true,
            true,
            false
        )
), upserted_customers AS (
    INSERT INTO customers (
        id,
        external_id,
        full_name,
        timezone,
        phone_number,
        email,
        sms_consent,
        call_consent,
        email_consent,
        opted_out
    )
    SELECT
        id,
        external_id,
        full_name,
        timezone,
        phone_number,
        email,
        sms_consent,
        call_consent,
        email_consent,
        opted_out
    FROM seed_customers
    ON CONFLICT (external_id) DO UPDATE SET
        full_name = EXCLUDED.full_name,
        timezone = EXCLUDED.timezone,
        phone_number = EXCLUDED.phone_number,
        email = EXCLUDED.email,
        sms_consent = EXCLUDED.sms_consent,
        call_consent = EXCLUDED.call_consent,
        email_consent = EXCLUDED.email_consent,
        opted_out = EXCLUDED.opted_out,
        updated_at = now()
    RETURNING id, external_id
), all_seed_customers AS (
    SELECT id, external_id FROM upserted_customers
    UNION
    SELECT customers.id, customers.external_id
    FROM customers
    JOIN seed_customers USING (external_id)
), seed_accounts (
    id,
    external_id,
    customer_external_id,
    status,
    balance_cents,
    days_past_due
) AS (
    VALUES
        (
            '00000000-0000-4000-9000-000000000001'::uuid,
            'acct_jane_doe_001',
            'cust_jane_doe_001',
            'delinquent',
            12500,
            14
        ),
        (
            '00000000-0000-4000-9000-000000000002'::uuid,
            'acct_robert_smith_001',
            'cust_robert_smith_001',
            'current',
            8750,
            0
        ),
        (
            '00000000-0000-4000-9000-000000000003'::uuid,
            'acct_maria_santos_001',
            'cust_maria_santos_001',
            'resolved',
            0,
            0
        ),
        (
            '00000000-0000-4000-9000-000000000004'::uuid,
            'acct_sam_taylor_001',
            'cust_sam_taylor_001',
            'delinquent',
            5400,
            7
        ),
        (
            '00000000-0000-4000-9000-000000000005'::uuid,
            'acct_alex_johnson_001',
            'cust_alex_johnson_001',
            'paused',
            23100,
            21
        ),
        (
            '00000000-0000-4000-9000-000000000006'::uuid,
            'acct_taylor_lee_001',
            'cust_taylor_lee_001',
            'paused',
            19000,
            30
        )
)
INSERT INTO accounts (
    id,
    external_id,
    customer_id,
    status,
    balance_cents,
    days_past_due
)
SELECT
    seed_accounts.id,
    seed_accounts.external_id,
    all_seed_customers.id,
    seed_accounts.status,
    seed_accounts.balance_cents,
    seed_accounts.days_past_due
FROM seed_accounts
JOIN all_seed_customers
    ON all_seed_customers.external_id = seed_accounts.customer_external_id
WHERE true
ON CONFLICT (external_id) DO UPDATE SET
    customer_id = EXCLUDED.customer_id,
    status = EXCLUDED.status,
    balance_cents = EXCLUDED.balance_cents,
    days_past_due = EXCLUDED.days_past_due,
    updated_at = now();

COMMIT;
