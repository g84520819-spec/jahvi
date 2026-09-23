import os
import uuid
from pathlib import Path

from sqlalchemy import Boolean, Column, Integer, String, Text, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")
if not DATABASE_URL.startswith("postgresql"):
    raise RuntimeError("DATABASE_URL must point to a PostgreSQL database")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class DashboardEffect(Base):
    """
    One unified effect pool for every user — no more per-tier filtering.
    `filter` holds the whole FFmpeg filter chain as one string, two
    sections joined by a comma: the base/global section, then the
    per-beat/stamp section. The per-beat section carries its own
    ``:enable='{STAMP}'`` clause already written into the text — the
    backend's job is just to substitute {STAMP} with the real
    between(t,...) expression built from that page's timestamps, never to
    construct the enable= wrapper itself. A GLOBAL-only effect's row
    simply has no {STAMP} placeholder in its second section at all, so no
    branching is needed to special-case it.
    """
    __tablename__ = "dashboard_effects"

    STAMP_PLACEHOLDER = "{STAMP}"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(40), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    filter = Column(Text, nullable=True)

    def to_payload(self):
        has_stamp = bool(self.filter) and self.STAMP_PLACEHOLDER in self.filter
        return {
            "class_id": self.id,
            "name": self.name,
            "states": ["global", "per_beat"] if has_stamp else ["global"],
        }

DEFAULT_CREDITS = 10


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String(120), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    credits = Column(Integer, nullable=False, default=DEFAULT_CREDITS, server_default=str(DEFAULT_CREDITS))


class SignupSecurity(Base):
    __tablename__ = "signup_security"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ip_address = Column(String(45), nullable=False, index=True)
    device_id = Column(String(128), nullable=False, index=True)
    success = Column(Boolean, nullable=False, default=False)


class CreditPurchase(Base):
    """One row per Paystack credit-pack purchase attempt. `credits` and
    `amount_kobo` are snapshotted at creation time so a later change to
    pack pricing never retroactively reinterprets an old purchase."""
    __tablename__ = "credit_purchases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    reference = Column(String(64), unique=True, nullable=False, index=True)
    pack = Column(Integer, nullable=False)
    credits = Column(Integer, nullable=False)
    amount_kobo = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="pending", server_default="pending")
    created_at = Column(Text, nullable=True)
    verified_at = Column(Text, nullable=True)


class QueuedJob(Base):
    """A render request that couldn't run immediately because server load
    was above threshold at submit time. Drained by the 3-minute worker,
    FIFO, no per-edit-type lanes. `payload` holds whatever that edit page
    would normally send synchronously (effect id, timestamps, gap
    settings, etc.) as JSON, since each edit_type's params look nothing
    alike."""
    __tablename__ = "queued_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, nullable=False, index=True)
    edit_type = Column(String(20), nullable=False)  # 'dashboard' | 'extraction' | 'sync_lab'
    payload = Column(Text, nullable=False)  # JSON
    input_file_key = Column(String(128), nullable=True)  # UploadThing key for the source video
    status = Column(String(20), nullable=False, default="queued", server_default="queued")
    output_file_key = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(Text, nullable=True)
    started_at = Column(Text, nullable=True)
    completed_at = Column(Text, nullable=True)


class UploadedFileLog(Base):
    """Source of truth for how old a file on UploadThing is, since
    UploadThing's own list-files response doesn't reliably expose an
    uploaded-at timestamp. Written every time anything is pushed to
    UploadThing, regardless of whether the job it belongs to later
    succeeds, fails, or gets orphaned — the orphan sweeper cross-references
    this against what's actually still on UploadThing."""
    __tablename__ = "uploaded_file_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_key = Column(String(128), unique=True, nullable=False, index=True)
    job_id = Column(String(36), nullable=True, index=True)
    uploaded_at = Column(Text, nullable=False)


class PasswordReset(Base):
    """Token-based, time-limited, single-use password reset."""
    __tablename__ = "password_resets"

    token = Column(String(64), primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    expires_at = Column(Text, nullable=False)
    used = Column(Boolean, nullable=False, default=False, server_default="false")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    run_migrations()

    with SessionLocal() as db:
        seed_dashboard_effects(db)


def seed_dashboard_effects(db):
    # (category, name, base_filter, per_beat_filter_with_stamp_placeholder_or_None)
    # A None per-beat filter means this effect is GLOBAL only — its row's
    # `filter` value is just the base section, no comma, no {STAMP} at all.
    defaults = [
        ("Rage", "Volt Rush", "eq=contrast=1.25:saturation=1.7:gamma=1.12", "hue=s=1.35"),
        ("Rage", "Bone Breaker", "eq=contrast=1.35:saturation=1.9:gamma=1.15", "curves=vintage"),
        ("Cinematic", "Night Fade", "eq=contrast=1.1:saturation=1.15:gamma=1.08", "colorbalance=rs=0.12:gs=0.0:bs=0.08"),
        ("Cinematic", "Amber Frame", "eq=contrast=1.18:saturation=1.25:gamma=1.1", "colorbalance=rs=0.18:gs=0.04:bs=-0.08"),
        ("Phonk", "Midnight Bass", "eq=contrast=1.3:saturation=1.55:gamma=1.08", "hue=s=1.3"),
        ("Phonk", "Dark Velocity", "eq=contrast=1.4:saturation=1.8:gamma=1.12", "colorbalance=rs=0.08:gs=-0.02:bs=0.16"),
        ("Snap", "Flash Pop", "eq=contrast=1.18:saturation=1.45:gamma=1.06", "hue=s=1.2"),
        ("Snap", "Impact Frame", "eq=contrast=1.35:saturation=1.7:gamma=1.1", "unsharp=5:5:0.8:5:5:0.0"),
        ("Cinematic", "Platinum Grade", "eq=contrast=1.45:saturation=1.35:gamma=1.08", "colorbalance=rs=0.2:gs=0.08:bs=-0.04"),
        ("Phonk", "Neon Run", "eq=contrast=1.5:saturation=1.9:gamma=1.12", "hue=s=1.55"),
        ("Rage", "Overdrive", "eq=contrast=1.55:saturation=2.0:gamma=1.15", "unsharp=5:5:1.0:5:5:0.0"),
        ("Snap", "Hypercut", "eq=contrast=1.5:saturation=1.85:gamma=1.1", "hue=s=1.5"),
    ]

    for category, name, base_filter, per_beat_filter in defaults:
        exists = db.query(DashboardEffect).filter_by(category=category, name=name).first()
        if not exists:
            if per_beat_filter:
                filter_value = f"{base_filter},{per_beat_filter}:enable='{DashboardEffect.STAMP_PLACEHOLDER}'"
            else:
                filter_value = base_filter
            db.add(DashboardEffect(category=category, name=name, filter=filter_value))
    db.commit()


def _split_sql_statements(sql: str) -> list[str]:
    """
    Splits a migration file into individual statements on top-level ';'
    characters, WITHOUT splitting inside a dollar-quoted block (```$$ ...
    $$```, used by DO blocks / PL/pgSQL function bodies) — a naive
    ``sql.split(";")`` would shred any semicolons inside such a block into
    separate, invalid fragments.
    """
    statements = []
    current = []
    in_dollar_quote = False
    i = 0
    while i < len(sql):
        if sql[i:i + 2] == "$$":
            in_dollar_quote = not in_dollar_quote
            current.append("$$")
            i += 2
            continue
        if sql[i] == ";" and not in_dollar_quote:
            statements.append("".join(current))
            current = []
            i += 1
            continue
        current.append(sql[i])
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def run_migrations():
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(255) PRIMARY KEY,
                    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

        applied_versions = {
            row.version
            for row in connection.execute(text("SELECT version FROM schema_migrations"))
        }

        for migration_file in migration_files:
            version = migration_file.name
            if version in applied_versions:
                continue

            migration_sql = migration_file.read_text(encoding="utf-8")
            for statement in _split_sql_statements(migration_sql):
                statement = statement.strip()
                if statement:
                    connection.exec_driver_sql(statement)

            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": version},
            )
