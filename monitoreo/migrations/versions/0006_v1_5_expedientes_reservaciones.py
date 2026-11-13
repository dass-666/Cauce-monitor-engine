"""v1.5: expedientes, reservaciones de laboratorio y su historial

Revision ID: 0006_v1_5
Revises: 0005_v1_4
Create Date: 2026-11-13 02:10:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0006_v1_5'
down_revision = '0005_v1_4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Solo crea tablas nuevas: no modifica ni borra datos de versiones anteriores.
    op.create_table('expedientes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('codigo', sa.String(length=32), nullable=False),
    sa.Column('id_externo', sa.String(length=64), nullable=True),
    sa.Column('id_registro_previo', sa.String(length=32), nullable=True),
    sa.Column('nombre', sa.String(length=128), nullable=False),
    sa.Column('fecha_nacimiento', sa.Date(), nullable=False),
    sa.Column('telefono', sa.String(length=16), nullable=False),
    sa.Column('correo', sa.String(length=128), nullable=False),
    sa.Column('region', sa.String(length=32), nullable=False),
    sa.Column('sucursal', sa.String(length=32), nullable=True),
    sa.Column('tipo_tramite', sa.String(length=32), nullable=False),
    sa.Column('operacion', sa.String(length=16), nullable=False),
    sa.Column('estado', sa.String(length=16), nullable=False),
    sa.Column('prioridad', sa.Integer(), nullable=False),
    sa.Column('operator_id', sa.Integer(), nullable=True),
    sa.Column('request_id', sa.Integer(), nullable=True),
    sa.Column('dedup_activo', sa.String(length=64), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('creado_por', sa.String(length=64), nullable=False),
    sa.Column('ultimo_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['operator_id'], ['operators.id'], ),
    sa.ForeignKeyConstraint(['region'], ['regions.code'], ),
    sa.ForeignKeyConstraint(['request_id'], ['requests.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('codigo'),
    sa.UniqueConstraint('dedup_activo'),
    sa.UniqueConstraint('id_externo'),
    sa.UniqueConstraint('request_id')
    )
    with op.batch_alter_table('expedientes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_expedientes_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_expedientes_estado'), ['estado'], unique=False)
        batch_op.create_index(batch_op.f('ix_expedientes_operator_id'), ['operator_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_expedientes_region'), ['region'], unique=False)

    op.create_table('reservaciones',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('expediente_id', sa.Integer(), nullable=False),
    sa.Column('viva_expediente_id', sa.Integer(), nullable=True),
    sa.Column('assignment_id', sa.Integer(), nullable=True),
    sa.Column('operator_id', sa.Integer(), nullable=True),
    sa.Column('estado', sa.String(length=16), nullable=False),
    sa.Column('contrato', sa.String(length=64), nullable=False),
    sa.Column('sesion_portal', sa.String(length=128), nullable=True),
    sa.Column('horario_id', sa.String(length=64), nullable=True),
    sa.Column('sucursal', sa.String(length=32), nullable=True),
    sa.Column('fecha', sa.String(length=10), nullable=True),
    sa.Column('hora', sa.String(length=5), nullable=True),
    sa.Column('clave', sa.String(length=64), nullable=True),
    sa.Column('folio', sa.String(length=32), nullable=True),
    sa.Column('vence_en', sa.DateTime(), nullable=True),
    sa.Column('confirmando_desde', sa.DateTime(), nullable=True),
    sa.Column('evidencia', sa.JSON(), nullable=False),
    sa.Column('error_codigo', sa.String(length=32), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('intentos_confirmacion', sa.Integer(), nullable=False),
    sa.Column('confirmada_por', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assignment_id'], ['assignments.id'], ),
    sa.ForeignKeyConstraint(['expediente_id'], ['expedientes.id'], ),
    sa.ForeignKeyConstraint(['operator_id'], ['operators.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('clave'),
    sa.UniqueConstraint('folio'),
    sa.UniqueConstraint('viva_expediente_id')
    )
    with op.batch_alter_table('reservaciones', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reservaciones_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_reservaciones_estado'), ['estado'], unique=False)
        batch_op.create_index(batch_op.f('ix_reservaciones_expediente_id'), ['expediente_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_reservaciones_operator_id'), ['operator_id'], unique=False)

    op.create_table('expediente_eventos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('expediente_id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('tipo', sa.String(length=32), nullable=False),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('assignment_id', sa.Integer(), nullable=True),
    sa.Column('reservacion_id', sa.Integer(), nullable=True),
    sa.Column('change_event_id', sa.Integer(), nullable=True),
    sa.Column('detalle', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['assignment_id'], ['assignments.id'], ),
    sa.ForeignKeyConstraint(['change_event_id'], ['change_events.id'], ),
    sa.ForeignKeyConstraint(['expediente_id'], ['expedientes.id'], ),
    sa.ForeignKeyConstraint(['reservacion_id'], ['reservaciones.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('expediente_eventos', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_expediente_eventos_expediente_id'), ['expediente_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_expediente_eventos_tipo'), ['tipo'], unique=False)
        batch_op.create_index(batch_op.f('ix_expediente_eventos_ts'), ['ts'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('expediente_eventos', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_expediente_eventos_ts'))
        batch_op.drop_index(batch_op.f('ix_expediente_eventos_tipo'))
        batch_op.drop_index(batch_op.f('ix_expediente_eventos_expediente_id'))

    op.drop_table('expediente_eventos')
    with op.batch_alter_table('reservaciones', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reservaciones_operator_id'))
        batch_op.drop_index(batch_op.f('ix_reservaciones_expediente_id'))
        batch_op.drop_index(batch_op.f('ix_reservaciones_estado'))
        batch_op.drop_index(batch_op.f('ix_reservaciones_created_at'))

    op.drop_table('reservaciones')
    with op.batch_alter_table('expedientes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_expedientes_region'))
        batch_op.drop_index(batch_op.f('ix_expedientes_operator_id'))
        batch_op.drop_index(batch_op.f('ix_expedientes_estado'))
        batch_op.drop_index(batch_op.f('ix_expedientes_created_at'))

    op.drop_table('expedientes')
