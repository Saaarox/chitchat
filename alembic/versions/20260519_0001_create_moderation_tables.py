"""Create moderation tables

Revision ID: 20260519_0001
Revises:
Create Date: 2026-05-19 05:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260519_0001"
down_revision = None
branch_labels = None
depends_on = None


group_member_role = postgresql.ENUM(
    "owner",
    "admin",
    "mod",
    "cleaner",
    "trusted",
    "member",
    name="group_member_role",
)


def upgrade() -> None:
    bind = op.get_bind()
    group_member_role.create(bind, checkfirst=True)

    op.create_table(
        "groups",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("log_channel_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("chat_id", name=op.f("pk_groups")),
    )

    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column(
            "is_banned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "warn_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("warn_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=False)

    op.create_table(
        "group_members",
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
        "role",
        postgresql.ENUM(
        "owner", "admin", "mod", "cleaner", "trusted", "member",
        name="group_member_role",
        create_type=False,
    ),
        nullable=False,
        server_default=sa.text("'member'"),
    ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.chat_id"],
            name=op.f("fk_group_members_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_group_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "user_id", name=op.f("pk_group_members")),
    )

    op.create_table(
        "warnings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=1024), nullable=False),
        sa.Column("given_by", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["given_by"],
            ["users.user_id"],
            name=op.f("fk_warnings_given_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.chat_id"],
            name=op.f("fk_warnings_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_warnings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_warnings")),
    )
    op.create_index(op.f("ix_warnings_expires_at"), "warnings", ["expires_at"], unique=False)
    op.create_index(op.f("ix_warnings_given_by"), "warnings", ["given_by"], unique=False)
    op.create_index(op.f("ix_warnings_group_id"), "warnings", ["group_id"], unique=False)
    op.create_index(op.f("ix_warnings_user_id"), "warnings", ["user_id"], unique=False)

    op.create_table(
        "flood_logs",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "message_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.chat_id"],
            name=op.f("fk_flood_logs_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_flood_logs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "group_id",
            "window_start",
            name=op.f("pk_flood_logs"),
        ),
    )

    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("execute_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "done",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.chat_id"],
            name=op.f("fk_scheduled_tasks_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_scheduled_tasks_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_tasks")),
    )
    op.create_index(
        op.f("ix_scheduled_tasks_done"),
        "scheduled_tasks",
        ["done"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_tasks_execute_at"),
        "scheduled_tasks",
        ["execute_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_tasks_group_id"),
        "scheduled_tasks",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_tasks_task_type"),
        "scheduled_tasks",
        ["task_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_tasks_user_id"),
        "scheduled_tasks",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("scheduled_tasks")
    op.drop_table("flood_logs")
    op.drop_table("warnings")
    op.drop_table("group_members")
    op.drop_table("users")
    op.drop_table("groups")

    bind = op.get_bind()
    group_member_role.drop(bind, checkfirst=True)
