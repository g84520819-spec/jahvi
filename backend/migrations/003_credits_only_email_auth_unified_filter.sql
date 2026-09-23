-- Tiers removed entirely: credit-only pricing, one unified effect pool.
-- Phone number dropped: email + password auth everywhere.
--
-- IMPORTANT: this migration must be safe to run against BOTH a fresh
-- install (Base.metadata.create_all() already created users/
-- dashboard_effects using the NEW model — filter_1/filter_2/phone/plan
-- never existed) and a real upgrade from the old schema (those columns
-- exist and need backfilling). Every step that assumes the old schema is
-- guarded with an information_schema existence check first.

-- users.email: add if missing, backfill placeholder emails for any
-- pre-existing rows that don't have one yet, then enforce NOT NULL.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS email VARCHAR(255);

UPDATE users
SET email = 'user-' || id || '@jahvi.placeholder'
WHERE email IS NULL;

ALTER TABLE users
    ALTER COLUMN email SET NOT NULL;

ALTER TABLE users
    DROP COLUMN IF EXISTS phone,
    DROP COLUMN IF EXISTS plan;

-- Only add the unique constraint if nothing already enforces uniqueness
-- on email — on a fresh install, SQLAlchemy's Column(unique=True) already
-- created one automatically via create_all(), named users_email_key by
-- Postgres's own default convention, so adding it again would collide.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'users' AND constraint_type = 'UNIQUE' AND constraint_name = 'users_email_key'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
    END IF;
END $$;

-- dashboard_effects.filter: add if missing (fresh install already has
-- it). Only attempt the filter_1/filter_2 backfill if those old columns
-- actually exist — on a fresh install they never did, so skip entirely.
ALTER TABLE dashboard_effects
    ADD COLUMN IF NOT EXISTS filter TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'dashboard_effects' AND column_name = 'filter_1'
    ) THEN
        UPDATE dashboard_effects
        SET filter = CASE
            WHEN filter_2 IS NOT NULL AND filter_2 <> '' THEN filter_1 || ',' || filter_2 || ':enable=''{STAMP}'''
            ELSE filter_1
        END
        WHERE filter IS NULL;
    END IF;
END $$;

ALTER TABLE dashboard_effects
    DROP COLUMN IF EXISTS filter_1,
    DROP COLUMN IF EXISTS filter_2,
    DROP COLUMN IF EXISTS plan;
