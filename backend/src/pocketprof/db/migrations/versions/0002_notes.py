"""notes (M3, FR-8)

Revision ID: 0002_notes
Revises: 0e3210cdce80
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op

revision = '0002_notes'
down_revision = '0e3210cdce80'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('notes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('selection', sa.JSON(), nullable=False),
    sa.Column('scope_label', sa.String(length=200), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('sections', sa.JSON(), nullable=False),
    sa.Column('sources', sa.JSON(), nullable=False),
    sa.Column('outcome', sa.String(length=16), nullable=False),
    sa.Column('notice', sa.String(length=500), nullable=True),
    sa.Column('truncated', sa.Boolean(), nullable=False),
    sa.Column('model', sa.String(length=160), nullable=True),
    sa.Column('config_hash', sa.String(length=64), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("outcome IN ('generated','refused','fallback')", name='ck_notes_outcome'),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notes_course_id'), ['course_id'], unique=False)
        batch_op.create_index('ix_notes_user_course', ['user_id', 'course_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notes_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notes_user_id'))
        batch_op.drop_index('ix_notes_user_course')
        batch_op.drop_index(batch_op.f('ix_notes_course_id'))
    op.drop_table('notes')
