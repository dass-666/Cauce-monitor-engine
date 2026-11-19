"""v2.0: adaptadores P2 (capturas manuales)

Revision ID: 0011_v2_0
Revises: 0010_v2_0
Create Date: 2026-11-13 16:30:00

AÑADE la tabla `capturas_manuales` (resultados que el operador registra tras hacer el trámite a
mano en el flujo oficial). No toca ni borra datos. El downgrade la quita.
"""
from alembic import op

import sqlalchemy as sa

revision = '0011_v2_0'
down_revision = '0010_v2_0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'capturas_manuales',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('creada', sa.DateTime(), nullable=False),
        sa.Column('adaptador', sa.String(length=32), nullable=False),
        sa.Column('expediente_id', sa.Integer(), nullable=True),
        sa.Column('atencion_id', sa.Integer(), nullable=True),
        sa.Column('operador', sa.String(length=64), nullable=False),
        sa.Column('folio', sa.String(length=64), nullable=False),
        sa.Column('fecha', sa.String(length=10), nullable=False),
        sa.Column('sucursal', sa.String(length=32), nullable=True),
        sa.Column('transaccion_id', sa.String(length=64), nullable=True),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('verificada_en', sa.DateTime(), nullable=True),
        sa.Column('detalle', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['expediente_id'], ['expedientes.id']),
        sa.ForeignKeyConstraint(['atencion_id'], ['atenciones.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('adaptador', 'folio', name='uq_captura_manual_folio'),
    )
    with op.batch_alter_table('capturas_manuales', schema=None) as b:
        b.create_index(b.f('ix_capturas_manuales_creada'), ['creada'], unique=False)
        b.create_index(b.f('ix_capturas_manuales_expediente_id'), ['expediente_id'], unique=False)
        b.create_index(b.f('ix_capturas_manuales_estado'), ['estado'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('capturas_manuales', schema=None) as b:
        b.drop_index(b.f('ix_capturas_manuales_estado'))
        b.drop_index(b.f('ix_capturas_manuales_expediente_id'))
        b.drop_index(b.f('ix_capturas_manuales_creada'))
    op.drop_table('capturas_manuales')
