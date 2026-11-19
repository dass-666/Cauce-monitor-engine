"""v1.7: centro de operaciones, presencia de operadores, explicación de asignaciones y centro de notificaciones

Revision ID: 0008_v1_7
Revises: 0007_v1_6
Create Date: 2026-11-13 07:30:00

AÑADE columnas (no borra datos) y clasifica las alertas existentes (rellena categoria y nivel;
el resto de cada alerta no cambia). El downgrade quita solo las columnas nuevas.
  operators.visto_en          última actividad del operador en el panel (presencia)
  assignments.explicacion     por qué se eligió a ese operador (legible para el administrador)
  alerts.categoria/nivel      clasificación del centro de notificaciones
  alerts.grupo/repeticiones/ultima_en   agrupación de avisos repetidos
"""
from alembic import op
import json

import sqlalchemy as sa

# Copia fija de la clasificación de la v1.7 (una migración no debe depender del código que cambie después)
_AGOTA = ("AGOTADO", "REDUCCION", "CANCELACION", "DESAPARECIDO")
_NIVEL = {"NUEVA_DISPONIBILIDAD": "MEDIA", "DISPONIBILIDAD_AGOTADA": "BAJA", "EXPEDIENTE_ASIGNADO": "ALTA",
          "OPERACION_ABANDONADA": "ALTA", "ERROR_COMUNICACION": "ALTA", "PROBLEMA_MOTOR": "CRITICA"}


def _clasificar(kind, payload):
    p = payload or {}
    if kind == "DISPONIBILIDAD":
        return "DISPONIBILIDAD_AGOTADA" if p.get("cambio") in _AGOTA else "NUEVA_DISPONIBILIDAD"
    if kind == "ASIGNACION":
        return "EXPEDIENTE_ASIGNADO"
    ev = p.get("evento_sistema")
    if ev == "asignacion_inactiva":
        return "OPERACION_ABANDONADA"
    if ev in ("error", "circuito", "acceso"):
        return "ERROR_COMUNICACION"
    return "PROBLEMA_MOTOR"


revision = '0008_v1_7'
down_revision = '0007_v1_6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('operators', schema=None) as b:
        b.add_column(sa.Column('visto_en', sa.DateTime(), nullable=True))
    with op.batch_alter_table('assignments', schema=None) as b:
        b.add_column(sa.Column('explicacion', sa.JSON(), nullable=True))
    with op.batch_alter_table('alerts', schema=None) as b:
        b.add_column(sa.Column('categoria', sa.String(length=32), nullable=True))
        b.add_column(sa.Column('nivel', sa.String(length=8), nullable=True))
        b.add_column(sa.Column('grupo', sa.String(length=128), nullable=True))
        b.add_column(sa.Column('repeticiones', sa.Integer(), nullable=False, server_default='1'))
        b.add_column(sa.Column('ultima_en', sa.DateTime(), nullable=True))
        b.create_index('ix_alerts_categoria', ['categoria'], unique=False)
        b.create_index('ix_alerts_grupo', ['grupo'], unique=False)
    conn = op.get_bind()
    alerts = sa.table('alerts', sa.column('id', sa.Integer), sa.column('kind', sa.String),
                      sa.column('payload', sa.Text), sa.column('categoria', sa.String), sa.column('nivel', sa.String))
    filas = conn.execute(sa.text("SELECT id, kind, payload FROM alerts")).fetchall()
    for aid, kind, payload in filas:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {}
        cat = _clasificar(kind, payload if isinstance(payload, dict) else {})
        conn.execute(alerts.update().where(alerts.c.id == aid).values(categoria=cat, nivel=_NIVEL[cat]))


def downgrade() -> None:
    with op.batch_alter_table('alerts', schema=None) as b:
        b.drop_index('ix_alerts_grupo')
        b.drop_index('ix_alerts_categoria')
        for c in ('ultima_en', 'repeticiones', 'grupo', 'nivel', 'categoria'):
            b.drop_column(c)
    with op.batch_alter_table('assignments', schema=None) as b:
        b.drop_column('explicacion')
    with op.batch_alter_table('operators', schema=None) as b:
        b.drop_column('visto_en')
