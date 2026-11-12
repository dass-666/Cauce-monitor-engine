"""v1.4: actividad de las asignaciones y resultado persistente de las operaciones

Revision ID: 0005_v1_4
Revises: 0004_v1_3
Create Date: 2026-11-12 23:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005_v1_4'
down_revision = '0004_v1_3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Solo añade columnas que admiten nulos y una tabla nueva: no reescribe ni borra datos.
    with op.batch_alter_table('assignments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('taken_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('taken_by', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('last_activity_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('inactive_alerted_at', sa.DateTime(), nullable=True))
    op.create_table(
        'operations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('op_key', sa.String(length=80), nullable=False),
        sa.Column('action', sa.String(length=32), nullable=False),
        sa.Column('target', sa.String(length=64), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username', 'op_key', name='uq_operations_user_key'),
    )
    with op.batch_alter_table('operations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_operations_created_at'), ['created_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('operations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_operations_created_at'))
    op.drop_table('operations')
    with op.batch_alter_table('assignments', schema=None) as batch_op:
        batch_op.drop_column('inactive_alerted_at')
        batch_op.drop_column('last_activity_at')
        batch_op.drop_column('taken_by')
        batch_op.drop_column('taken_at')
