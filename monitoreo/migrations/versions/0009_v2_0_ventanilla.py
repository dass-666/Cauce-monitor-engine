"""v2.0: atención presencial (modo ventanilla)

Revision ID: 0009_v2_0
Revises: 0008_v1_7
Create Date: 2026-11-13 09:00:00

AÑADE la tabla `atenciones` (no toca ni borra datos existentes). El downgrade la quita.
"""
from alembic import op

import sqlalchemy as sa

revision = '0009_v2_0'
down_revision = '0008_v1_7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'atenciones',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('codigo', sa.String(length=32), nullable=False),
        sa.Column('clave', sa.String(length=64), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('operator_id', sa.Integer(), nullable=True),
        sa.Column('expediente_id', sa.Integer(), nullable=True),
        sa.Column('viva_expediente_id', sa.Integer(), nullable=True),
        sa.Column('reservacion_id', sa.Integer(), nullable=True),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('resultado', sa.String(length=32), nullable=True),
        sa.Column('folio', sa.String(length=32), nullable=True),
        sa.Column('sucursal', sa.String(length=32), nullable=True),
        sa.Column('region', sa.String(length=32), nullable=True),
        sa.Column('conexion', sa.String(length=64), nullable=True),
        sa.Column('pasos', sa.Integer(), nullable=False),
        sa.Column('iniciada_en', sa.DateTime(), nullable=False),
        sa.Column('cerrada_en', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['expediente_id'], ['expedientes.id']),
        sa.ForeignKeyConstraint(['operator_id'], ['operators.id']),
        sa.ForeignKeyConstraint(['reservacion_id'], ['reservaciones.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('codigo'),
        sa.UniqueConstraint('username', 'clave', name='uq_atenciones_usuario_clave'),
        sa.UniqueConstraint('viva_expediente_id'),
    )
    with op.batch_alter_table('atenciones', schema=None) as b:
        b.create_index(b.f('ix_atenciones_estado'), ['estado'], unique=False)
        b.create_index(b.f('ix_atenciones_expediente_id'), ['expediente_id'], unique=False)
        b.create_index(b.f('ix_atenciones_iniciada_en'), ['iniciada_en'], unique=False)
        b.create_index(b.f('ix_atenciones_operator_id'), ['operator_id'], unique=False)
        b.create_index(b.f('ix_atenciones_region'), ['region'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('atenciones', schema=None) as b:
        for ix in ('region', 'operator_id', 'iniciada_en', 'expediente_id', 'estado'):
            b.drop_index(b.f(f'ix_atenciones_{ix}'))
    op.drop_table('atenciones')
