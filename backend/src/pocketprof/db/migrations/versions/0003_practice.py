"""practice items, schedule, attempts, mastery (M4, FR-10, FR-17, FR-18)

Revision ID: 0003_practice
Revises: 0002_notes
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op

revision = '0003_practice'
down_revision = '0002_notes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('mastery',
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('student_ref', sa.String(length=16), nullable=False),
    sa.Column('topic', sa.String(length=200), nullable=False),
    sa.Column('estimate', sa.Float(), nullable=False),
    sa.Column('n_attempts', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('estimate >= 0 AND estimate <= 1', name='ck_mastery_estimate'),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('course_id', 'student_ref', 'topic')
    )
    op.create_table('practice_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('material_id', sa.Integer(), nullable=True),
    sa.Column('chunk_id', sa.String(length=120), nullable=False),
    sa.Column('file_hash', sa.String(length=64), nullable=True),
    sa.Column('topic', sa.String(length=200), nullable=True),
    sa.Column('week', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('options', sa.JSON(), nullable=True),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=True),
    sa.Column('source', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('reject_reason', sa.String(length=500), nullable=True),
    sa.Column('edited', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('validated_by', sa.Integer(), nullable=True),
    sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('retired_reason', sa.String(length=200), nullable=True),
    sa.Column('model', sa.String(length=160), nullable=True),
    sa.Column('config_hash', sa.String(length=64), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("kind IN ('mcq','tf','short')", name='ck_items_kind'),
    sa.CheckConstraint("status IN ('draft','validated','released','rejected','retired')", name='ck_items_status'),
    sa.CheckConstraint("status NOT IN ('validated','released') OR validated_by IS NOT NULL", name='ck_items_validated_by'),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['validated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('practice_items', schema=None) as batch_op:
        batch_op.create_index('ix_practice_items_course_status_topic', ['course_id', 'status', 'topic'], unique=False)
    op.create_table('practice_attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('student_ref', sa.String(length=16), nullable=False),
    sa.Column('topic', sa.String(length=200), nullable=True),
    sa.Column('week', sa.Integer(), nullable=True),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('correct', sa.Boolean(), nullable=False),
    sa.Column('self_marked', sa.Boolean(), nullable=False),
    sa.Column('box_before', sa.Integer(), nullable=False),
    sa.Column('box_after', sa.Integer(), nullable=False),
    sa.Column('next_due', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_id'], ['practice_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('practice_attempts', schema=None) as batch_op:
        batch_op.create_index('ix_practice_attempts_course_ref_topic', ['course_id', 'student_ref', 'topic'], unique=False)
        batch_op.create_index(batch_op.f('ix_practice_attempts_item_id'), ['item_id'], unique=False)
    op.create_table('practice_schedule',
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('student_ref', sa.String(length=16), nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('box', sa.Integer(), nullable=False),
    sa.Column('next_due', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('n_attempts', sa.Integer(), nullable=False),
    sa.CheckConstraint('box BETWEEN 1 AND 5', name='ck_schedule_box'),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_id'], ['practice_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('item_id', 'student_ref')
    )
    with op.batch_alter_table('practice_schedule', schema=None) as batch_op:
        batch_op.create_index('ix_practice_schedule_ref_due', ['student_ref', 'next_due'], unique=False)

    # ### end Alembic commands ###


def downgrade() -> None:
    with op.batch_alter_table('practice_schedule', schema=None) as batch_op:
        batch_op.drop_index('ix_practice_schedule_ref_due')
    op.drop_table('practice_schedule')
    with op.batch_alter_table('practice_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_practice_attempts_item_id'))
        batch_op.drop_index('ix_practice_attempts_course_ref_topic')
    op.drop_table('practice_attempts')
    with op.batch_alter_table('practice_items', schema=None) as batch_op:
        batch_op.drop_index('ix_practice_items_course_status_topic')
    op.drop_table('practice_items')
    op.drop_table('mastery')
    # ### end Alembic commands ###
