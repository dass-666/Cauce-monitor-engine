"""v1.3: estado persistente del conector y momento de procesamiento de cada evento

Revision ID: 0004_v1_3
Revises: 0003_v1_2
Create Date: 2026-11-12 19:40:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004_v1_3'
down_revision = '0003_v1_2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Solo añade columnas que admiten nulos: no reescribe ni borra datos existentes.
    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.add_column(sa.Column('connector_state', sa.JSON(), nullable=True))
    with op.batch_alter_table('change_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('processed_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('change_events', schema=None) as batch_op:
        batch_op.drop_column('processed_at')
    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.drop_column('connector_state')
