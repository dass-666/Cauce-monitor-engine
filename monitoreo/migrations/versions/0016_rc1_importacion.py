"""RC1 · importación administrativa por lotes y fechas aceptables del expediente

- expedientes.fecha_solicitada / fechas_desde / fechas_hasta: lo que la persona pidió y el rango que
  acepta. Separados de la identificación y de la fecha CONFIRMADA (que vive en la reservación).
- importaciones: un lote por archivo cargado (huella SHA-256 del archivo, hoja, asignación de columnas).
- importacion_filas: resultado de cada fila (creada, ya existía, rechazada) con sus errores por campo.
  Sin valores personales: solo número de fila, campos y mensajes. `clave_creado` es UNIQUE: una misma
  persona del archivo no crea dos expedientes aunque se cargue dos veces, ni a la vez.

Revision ID: 0016_rc1
Revises: 0015_v2_2
"""
import sqlalchemy as sa
from alembic import op

revision = "0016_rc1"
down_revision = "0015_v2_2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("expedientes", schema=None) as b:
        b.add_column(sa.Column("fecha_solicitada", sa.Date(), nullable=True))
        b.add_column(sa.Column("fechas_desde", sa.Date(), nullable=True))
        b.add_column(sa.Column("fechas_hasta", sa.Date(), nullable=True))
    op.create_table(
        "importaciones",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("codigo", sa.String(32), nullable=False, unique=True),
        sa.Column("archivo", sa.String(128), nullable=False),
        sa.Column("archivo_sha256", sa.String(64), nullable=False, index=True),
        sa.Column("formato", sa.String(8), nullable=False),
        sa.Column("hoja", sa.String(64), nullable=True),
        sa.Column("mapeo", sa.JSON, nullable=False),
        sa.Column("formato_fecha", sa.String(8), nullable=False),
        sa.Column("total", sa.Integer, nullable=False),
        sa.Column("creadas", sa.Integer, nullable=False),
        sa.Column("existentes", sa.Integer, nullable=False),
        sa.Column("rechazadas", sa.Integer, nullable=False),
        sa.Column("avisos", sa.JSON, nullable=False),
        sa.Column("creado_por", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, index=True),
    )
    op.create_table(
        "importacion_filas",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("importacion_id", sa.Integer, sa.ForeignKey("importaciones.id"), nullable=False, index=True),
        sa.Column("fila", sa.Integer, nullable=False),
        sa.Column("resultado", sa.String(24), nullable=False, index=True),
        sa.Column("expediente_id", sa.Integer, sa.ForeignKey("expedientes.id"), nullable=True, index=True),
        sa.Column("clave_fila", sa.String(64), nullable=True, index=True),
        sa.Column("clave_creado", sa.String(64), nullable=True, unique=True),
        sa.Column("estado_expediente", sa.String(24), nullable=True),
        sa.Column("errores", sa.JSON, nullable=False),
        sa.Column("faltan", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("importacion_filas")
    op.drop_table("importaciones")
    with op.batch_alter_table("expedientes", schema=None) as b:
        b.drop_column("fechas_hasta")
        b.drop_column("fechas_desde")
        b.drop_column("fecha_solicitada")
