"""v1.2: testigo de reclamo en entregas (evita que un resultado tardío pise uno más nuevo)

Revision ID: 0003_v1_2
Revises: 0002_v1_1
Create Date: 2026-11-12 17:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0003_v1_2'
down_revision = '0002_v1_1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Solo añade una columna que admite nulos: no reescribe datos.
    with op.batch_alter_table('deliveries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('claim_token', sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('deliveries', schema=None) as batch_op:
        batch_op.drop_column('claim_token')
