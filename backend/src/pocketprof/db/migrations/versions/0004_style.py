"""teacher style profiles and exemplars (M5, FR-11)

Revision ID: 0004_style
Revises: 0003_practice
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op

revision = '0004_style'
down_revision = '0003_practice'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('style_exemplars',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('teacher_id', sa.Integer(), nullable=False),
    sa.Column('origin', sa.String(length=12), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('source_ref', sa.String(length=300), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("origin IN ('calibration','material')", name='ck_exemplar_origin'),
    sa.CheckConstraint("status IN ('pending','approved','rejected')", name='ck_exemplar_status'),
    sa.ForeignKeyConstraint(['teacher_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('style_exemplars', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_style_exemplars_teacher_id'), ['teacher_id'], unique=False)

    op.create_table('style_profiles',
    sa.Column('teacher_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('rules', sa.JSON(), nullable=False),
    sa.Column('calibration', sa.JSON(), nullable=False),
    sa.Column('measures', sa.JSON(), nullable=False),
    sa.Column('model', sa.String(length=160), nullable=True),
    sa.Column('config_hash', sa.String(length=64), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("status IN ('draft','active','off')", name='ck_style_status'),
    sa.ForeignKeyConstraint(['teacher_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('teacher_id')
    )


def downgrade() -> None:
    op.drop_table('style_profiles')
    with op.batch_alter_table('style_exemplars', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_style_exemplars_teacher_id'))

    op.drop_table('style_exemplars')
