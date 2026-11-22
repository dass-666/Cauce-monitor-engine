"""Piloto asistido · confirmación humana persistente de citas hechas a mano en el portal oficial

- confirmaciones_humanas: un registro por intento de una persona (abrir revisión, confirmar revisión,
  registrar la cita con folio y comprobante, o revocar). Sin datos personales: huellas SHA-256, folio,
  fecha, sucursal y referencias internas. `viva_expediente_id` UNIQUE: una sola abierta por expediente.
  La cita registrada vive además como reservación CONFIRMADA con contrato `oficial_asistido`.

Revision ID: 0017_asistido
Revises: 0016_rc1
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_asistido"
down_revision = "0016_rc1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "confirmaciones_humanas",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("expediente_id", sa.Integer, sa.ForeignKey("expedientes.id"), nullable=False, index=True),
        sa.Column("viva_expediente_id", sa.Integer, nullable=True, unique=True),
        sa.Column("reservacion_id", sa.Integer, sa.ForeignKey("reservaciones.id"), nullable=True, index=True),
        sa.Column("persona", sa.String(64), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False, index=True),
        sa.Column("huella_datos", sa.String(64), nullable=False),
        sa.Column("abierta_en", sa.DateTime, nullable=False, index=True),
        sa.Column("revisada_en", sa.DateTime, nullable=True),
        sa.Column("registrada_en", sa.DateTime, nullable=True),
        sa.Column("revocada_en", sa.DateTime, nullable=True),
        sa.Column("revocada_por", sa.String(64), nullable=True),
        sa.Column("folio", sa.String(32), nullable=True),
        sa.Column("fecha", sa.String(10), nullable=True),
        sa.Column("hora", sa.String(5), nullable=True),
        sa.Column("sucursal", sa.String(32), nullable=True),
        sa.Column("comprobante_sha256", sa.String(64), nullable=True),
        sa.Column("comprobante_ref", sa.String(64), nullable=True),
        sa.Column("detalle", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("confirmaciones_humanas")
