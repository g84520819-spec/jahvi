ALTER TABLE users
    DROP COLUMN IF EXISTS created_at;

ALTER TABLE signup_security
    DROP COLUMN IF EXISTS phone,
    DROP COLUMN IF EXISTS failure_reason,
    DROP COLUMN IF EXISTS user_id,
    DROP COLUMN IF EXISTS created_at;