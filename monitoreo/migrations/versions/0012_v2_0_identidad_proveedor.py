"""v2.0: identidad del proveedor por instancia

Revision ID: 0012_v2_0
Revises: 0011_v2_0
Create Date: 2026-11-13 18:00:00

AÑADE `reservaciones.proveedor` (nula en las existentes: se tratan como del proveedor registrado
en la base) y la tabla `instancias_motor`. No toca ni borra datos. El downgrade quita ambas.
"""
from alembic import op

import sqlalchemy as sa

revision = '0012_v2_0'
down_revision = '0011_v2_0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('reservaciones', schema=None) as b:
        b.add_column(sa.Column('proveedor', sa.String(length=32), nullable=True))
        b.create_index(b.f('ix_reservaciones_proveedor'), ['proveedor'], unique=False)
    op.create_table(
        'instancias_motor',
        sa.Column('instancia', sa.String(length=96), nullable=False),
        sa.Column('proveedor', sa.String(length=32), nullable=False),
        sa.Column('destino', sa.String(length=160), nullable=False),
        sa.Column('visto_en', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('instancia'),
    )
    with op.batch_alter_table('instancias_motor', schema=None) as b:
        b.create_index(b.f('ix_instancias_motor_visto_en'), ['visto_en'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('instancias_motor', schema=None) as b:
        b.drop_index(b.f('ix_instancias_motor_visto_en'))
    op.drop_table('instancias_motor')
    with op.batch_alter_table('reservaciones', schema=None) as b:
        b.drop_index(b.f('ix_reservaciones_proveedor'))
        b.drop_column('proveedor')
