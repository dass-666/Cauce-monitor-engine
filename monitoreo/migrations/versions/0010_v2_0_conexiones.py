"""v2.0: gestor de conexiones

Revision ID: 0010_v2_0
Revises: 0009_v2_0
Create Date: 2026-11-13 12:00:00

AÑADE la tabla `conexion_eventos` y la columna `reservaciones.conexion` (nula en las existentes).
No toca ni borra datos. El downgrade quita ambas.
"""
from alembic import op

import sqlalchemy as sa

revision = '0010_v2_0'
down_revision = '0009_v2_0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'conexion_eventos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ts', sa.DateTime(), nullable=False),
        sa.Column('evento', sa.String(length=16), nullable=False),
        sa.Column('conexion', sa.String(length=64), nullable=True),
        sa.Column('tipo', sa.String(length=16), nullable=True),
        sa.Column('operacion', sa.String(length=24), nullable=True),
        sa.Column('detalle', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('conexion_eventos', schema=None) as b:
        b.create_index(b.f('ix_conexion_eventos_ts'), ['ts'], unique=False)
        b.create_index(b.f('ix_conexion_eventos_evento'), ['evento'], unique=False)
    with op.batch_alter_table('reservaciones', schema=None) as b:
        b.add_column(sa.Column('conexion', sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('reservaciones', schema=None) as b:
        b.drop_column('conexion')
    with op.batch_alter_table('conexion_eventos', schema=None) as b:
        b.drop_index(b.f('ix_conexion_eventos_evento'))
        b.drop_index(b.f('ix_conexion_eventos_ts'))
    op.drop_table('conexion_eventos')
