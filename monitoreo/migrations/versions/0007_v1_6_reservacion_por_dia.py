"""v1.6: reservación por día, estados de expediente, calendario y permisos por sucursal

Revision ID: 0007_v1_6
Revises: 0006_v1_5
Create Date: 2026-11-13 04:30:00

No borra datos. Los expedientes de la v1.5 conservan su modo (POR_HORARIO) y sus estados se
traducen a los de la v1.6:
  PENDIENTE -> LISTO, EN_CAPTURA -> CAPTURA_EN_PROCESO, RESERVANDO -> RESERVACION_PENDIENTE,
  CONFIRMADO -> RESERVACION_CONFIRMADA, FALLIDO/CANCELADO -> CERRADO (con el motivo).
"""
from alembic import op
import sqlalchemy as sa


revision = '0007_v1_6'
down_revision = '0006_v1_5'
branch_labels = None
depends_on = None

ESTADOS = [("PENDIENTE", "LISTO", None), ("EN_CAPTURA", "CAPTURA_EN_PROCESO", None),
           ("RESERVANDO", "RESERVACION_PENDIENTE", None), ("CONFIRMADO", "RESERVACION_CONFIRMADA", None),
           ("FALLIDO", "CERRADO", "FALLIDO"), ("CANCELADO", "CERRADO", "CANCELADO")]


def upgrade() -> None:
    with op.batch_alter_table('operators', schema=None) as b:
        b.add_column(sa.Column('sucursales', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))

    with op.batch_alter_table('expedientes', schema=None) as b:
        b.alter_column('estado', existing_type=sa.String(length=16), type_=sa.String(length=24), existing_nullable=False)
        b.alter_column('fecha_nacimiento', existing_type=sa.Date(), nullable=True)
        b.alter_column('telefono', existing_type=sa.String(length=16), nullable=True)
        b.alter_column('correo', existing_type=sa.String(length=128), nullable=True)
        b.add_column(sa.Column('motivo_cierre', sa.String(length=24), nullable=True))
        b.add_column(sa.Column('folio_anterior', sa.String(length=32), nullable=True))
        b.add_column(sa.Column('modo', sa.String(length=12), nullable=False, server_default='POR_HORARIO'))
        b.add_column(sa.Column('sucursales_alternativas', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
        b.add_column(sa.Column('ronda', sa.Integer(), nullable=False, server_default='1'))
        b.add_column(sa.Column('validado_en', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('anonimizado_en', sa.DateTime(), nullable=True))

    with op.batch_alter_table('reservaciones', schema=None) as b:
        b.add_column(sa.Column('modo', sa.String(length=12), nullable=False, server_default='POR_HORARIO'))

    t = sa.table('expedientes', sa.column('estado', sa.String), sa.column('motivo_cierre', sa.String),
                 sa.column('validado_en', sa.DateTime), sa.column('created_at', sa.DateTime))
    for viejo, nuevo, motivo in ESTADOS:
        op.execute(t.update().where(t.c.estado == viejo).values(estado=nuevo, motivo_cierre=motivo))
    op.execute(t.update().values(validado_en=t.c.created_at))     # en la v1.5 se validaba al crear

    op.create_table('calendario_fuentes',
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('mecanismo', sa.String(length=12), nullable=False),
    sa.Column('conectado', sa.Boolean(), nullable=False),
    sa.Column('confirmado_en', sa.DateTime(), nullable=True),
    sa.Column('ultimo_intento_en', sa.DateTime(), nullable=True),
    sa.Column('ultimo_error_en', sa.DateTime(), nullable=True),
    sa.Column('ultimo_error_codigo', sa.String(length=32), nullable=True),
    sa.Column('ultimo_error', sa.Text(), nullable=True),
    sa.Column('ultima_secuencia', sa.Integer(), nullable=False),
    sa.Column('eventos_recibidos', sa.Integer(), nullable=False),
    sa.Column('fuera_de_orden', sa.Integer(), nullable=False),
    sa.Column('resincronizaciones', sa.Integer(), nullable=False),
    sa.Column('reconexiones', sa.Integer(), nullable=False),
    sa.Column('zona_horaria', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('source_id')
    )
    op.create_table('calendario_dias',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('clave', sa.String(length=96), nullable=False),
    sa.Column('region', sa.String(length=32), nullable=False),
    sa.Column('sucursal', sa.String(length=32), nullable=False),
    sa.Column('sucursal_nombre', sa.String(length=128), nullable=False),
    sa.Column('tramite', sa.String(length=32), nullable=False),
    sa.Column('fecha', sa.String(length=10), nullable=False),
    sa.Column('estado', sa.String(length=16), nullable=False),
    sa.Column('libres', sa.Integer(), nullable=False),
    sa.Column('capacidad', sa.Integer(), nullable=False),
    sa.Column('version_portal', sa.Integer(), nullable=False),
    sa.Column('disponible_hasta', sa.DateTime(), nullable=True),
    sa.Column('verificado_en', sa.DateTime(), nullable=False),
    sa.Column('cambiado_en', sa.DateTime(), nullable=False),
    sa.Column('ultima_causa', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'clave', name='uq_calendario_dia')
    )
    with op.batch_alter_table('calendario_dias', schema=None) as b:
        b.create_index(b.f('ix_calendario_dias_fecha'), ['fecha'], unique=False)
        b.create_index(b.f('ix_calendario_dias_region'), ['region'], unique=False)
        b.create_index(b.f('ix_calendario_dias_source_id'), ['source_id'], unique=False)
        b.create_index(b.f('ix_calendario_dias_sucursal'), ['sucursal'], unique=False)
    op.create_table('calendario_cambios',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('tipo', sa.String(length=32), nullable=False),
    sa.Column('clave', sa.String(length=96), nullable=True),
    sa.Column('sucursal', sa.String(length=32), nullable=True),
    sa.Column('tramite', sa.String(length=32), nullable=True),
    sa.Column('fecha', sa.String(length=10), nullable=True),
    sa.Column('antes', sa.String(length=16), nullable=True),
    sa.Column('despues', sa.String(length=16), nullable=True),
    sa.Column('libres', sa.Integer(), nullable=True),
    sa.Column('version_portal', sa.Integer(), nullable=True),
    sa.Column('mecanismo', sa.String(length=12), nullable=False),
    sa.Column('ts_portal', sa.DateTime(), nullable=True),
    sa.Column('detectado_en', sa.DateTime(), nullable=False),
    sa.Column('detalle', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('calendario_cambios', schema=None) as b:
        b.create_index(b.f('ix_calendario_cambios_detectado_en'), ['detectado_en'], unique=False)
        b.create_index(b.f('ix_calendario_cambios_source_id'), ['source_id'], unique=False)
        b.create_index(b.f('ix_calendario_cambios_tipo'), ['tipo'], unique=False)


def downgrade() -> None:
    op.drop_table('calendario_cambios')
    op.drop_table('calendario_dias')
    op.drop_table('calendario_fuentes')
    t = sa.table('expedientes', sa.column('estado', sa.String), sa.column('motivo_cierre', sa.String))
    for viejo, nuevo, motivo in ESTADOS:
        cond = (t.c.estado == nuevo) if motivo is None else ((t.c.estado == nuevo) & (t.c.motivo_cierre == motivo))
        op.execute(t.update().where(cond).values(estado=viejo))
    with op.batch_alter_table('reservaciones', schema=None) as b:
        b.drop_column('modo')
    with op.batch_alter_table('expedientes', schema=None) as b:
        for c in ('anonimizado_en', 'validado_en', 'ronda', 'sucursales_alternativas', 'modo', 'folio_anterior',
                  'motivo_cierre'):
            b.drop_column(c)
    with op.batch_alter_table('operators', schema=None) as b:
        b.drop_column('sucursales')
