"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-15

与 ``backend/main_api/models.py`` 保持一致的表结构（PostgreSQL 专用）。
JSON 列使用 ``JSONB``；时间戳为 ``TIMESTAMPTZ``，默认由应用侧写入。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=160), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("config_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("outline", sa.Text(), nullable=False),
        sa.Column("document_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_index("idx_projects_owner", "projects", ["owner_id", "updated_at"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])
    op.create_index("ix_jobs_owner_id", "jobs", ["owner_id"])
    op.create_index("idx_jobs_owner", "jobs", ["owner_id", "created_at"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"])
    op.create_index("idx_events_job", "job_events", ["job_id", "id"])

    op.create_table(
        "files",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("bucket", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_files_owner_id", "files", ["owner_id"])
    op.create_index("ix_files_project_id", "files", ["project_id"])
    op.create_index("idx_files_owner", "files", ["owner_id", "created_at"])
    op.create_index("idx_files_project", "files", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_table("files")
    op.drop_table("job_events")
    op.drop_table("jobs")
    op.drop_table("projects")
    op.drop_table("users")
