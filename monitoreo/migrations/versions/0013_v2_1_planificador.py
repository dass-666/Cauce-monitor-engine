"""v2.1 · planificador diario, observación de calendarios y coincidencias

Revision ID: 0013_v2_1
Revises: 0012_v2_0
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_v2_1"
down_revision = "0012_v2_0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "planes_diarios",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("fecha_jornada", sa.Date, nullable=False, unique=True),
        sa.Column("estado", sa.String(12), nullable=False),
        sa.Column("capacidad", sa.Integer, nullable=False),
        sa.Column("creado_por", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "plan_elementos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("planes_diarios.id"), nullable=False),
        sa.Column("expediente_id", sa.Integer, sa.ForeignKey("expedientes.id"), nullable=False),
        sa.Column("activo_expediente_id", sa.Integer, nullable=True, unique=True),
        sa.Column("estado", sa.String(24), nullable=False),
        sa.Column("prioridad", sa.Integer, nullable=False),
        sa.Column("orden", sa.Integer, nullable=False),
        sa.Column("tramite", sa.String(32), nullable=False),
        sa.Column("sucursales", sa.JSON, nullable=False),
        sa.Column("fechas_aceptables", sa.JSON, nullable=False),
        sa.Column("evento_id", sa.Integer, nullable=True),
        sa.Column("fecha_asignada", sa.String(10), nullable=True),
        sa.Column("sucursal_asignada", sa.String(32), nullable=True),
        sa.Column("operador_id", sa.Integer, nullable=True),
        sa.Column("reservacion_id", sa.Integer, nullable=True),
        sa.Column("explicacion", sa.JSON, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("plan_id", "expediente_id", name="uq_plan_expediente"),
    )
    op.create_index("ix_plan_elementos_plan_id", "plan_elementos", ["plan_id"])
    op.create_index("ix_plan_elementos_expediente_id", "plan_elementos", ["expediente_id"])
    op.create_index("ix_plan_elementos_estado", "plan_elementos", ["estado"])
    op.create_table(
        "observacion_dias",
        sa.Column("clave", sa.String(96), primary_key=True),
        sa.Column("sucursal", sa.String(32), nullable=False),
        sa.Column("tramite", sa.String(32), nullable=False),
        sa.Column("fecha", sa.String(10), nullable=False),
        sa.Column("estado", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("fuente", sa.String(12), nullable=False),
        sa.Column("actualizado_en", sa.DateTime, nullable=False),
    )
    op.create_table(
        "observacion_vistas",
        sa.Column("clave", sa.String(96), primary_key=True),
        sa.Column("estado", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("actualizado_en", sa.DateTime, nullable=False),
    )
    op.create_table(
        "observacion_eventos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("huella", sa.String(64), nullable=False, unique=True),
        sa.Column("tipo", sa.String(24), nullable=False),
        sa.Column("clave", sa.String(96), nullable=False),
        sa.Column("sucursal", sa.String(32), nullable=False),
        sa.Column("tramite", sa.String(32), nullable=False),
        sa.Column("fecha", sa.String(10), nullable=True),
        sa.Column("estado_anterior", sa.String(16), nullable=True),
        sa.Column("estado", sa.String(16), nullable=False),
        sa.Column("estrategia", sa.String(12), nullable=False),
        sa.Column("observador", sa.String(64), nullable=False),
        sa.Column("detectado_en", sa.DateTime, nullable=False),
        sa.Column("registrado_en", sa.DateTime, nullable=False),
        sa.Column("evidencia", sa.JSON, nullable=False),
        sa.Column("detecciones", sa.JSON, nullable=False),
        sa.Column("consumo", sa.String(16), nullable=False),
        sa.Column("plan_elemento_id", sa.Integer, nullable=True),
        sa.Column("explicacion", sa.JSON, nullable=False),
    )
    op.create_index("ix_observacion_eventos_tipo", "observacion_eventos", ["tipo"])
    op.create_index("ix_observacion_eventos_clave", "observacion_eventos", ["clave"])
    op.create_index("ix_observacion_eventos_registrado_en", "observacion_eventos", ["registrado_en"])
    op.create_index("ix_observacion_eventos_consumo", "observacion_eventos", ["consumo"])


def downgrade() -> None:
    op.drop_table("observacion_eventos")
    op.drop_table("observacion_vistas")
    op.drop_table("observacion_dias")
    op.drop_index("ix_plan_elementos_estado", "plan_elementos")
    op.drop_index("ix_plan_elementos_expediente_id", "plan_elementos")
    op.drop_index("ix_plan_elementos_plan_id", "plan_elementos")
    op.drop_table("plan_elementos")
    op.drop_table("planes_diarios")
