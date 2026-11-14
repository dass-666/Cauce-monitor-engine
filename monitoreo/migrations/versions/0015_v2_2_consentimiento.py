"""v2.2 · consentimiento verificable por expediente

Revision ID: 0015_v2_2
Revises: 0014_v2_1
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_v2_2"
down_revision = "0014_v2_1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consentimientos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("codigo", sa.String(32), nullable=False, unique=True),
        sa.Column("expediente_id", sa.Integer, sa.ForeignKey("expedientes.id"), nullable=False, index=True),
        sa.Column("tipo_tramite", sa.String(32), nullable=False),
        sa.Column("finalidad", sa.String(32), nullable=False),
        sa.Column("medio", sa.String(24), nullable=False),
        sa.Column("otorgado_en", sa.DateTime, nullable=False),
        sa.Column("vigente_hasta", sa.DateTime, nullable=True),
        sa.Column("responsable", sa.String(64), nullable=False),
        sa.Column("aviso_privacidad", sa.String(32), nullable=False),
        sa.Column("evidencia_sha256", sa.String(64), nullable=False),
        sa.Column("evidencia_ref", sa.String(64), nullable=False),
        sa.Column("estado", sa.String(12), nullable=False, index=True),
        sa.Column("revocado_en", sa.DateTime, nullable=True),
        sa.Column("revocado_por", sa.String(64), nullable=True),
        sa.Column("revocacion_medio", sa.String(24), nullable=True),
        sa.Column("revocacion_motivo", sa.String(32), nullable=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "consentimiento_eventos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("consentimiento_id", sa.Integer, sa.ForeignKey("consentimientos.id"), nullable=False, index=True),
        sa.Column("expediente_id", sa.Integer, nullable=False, index=True),
        sa.Column("tipo", sa.String(24), nullable=False),
        sa.Column("ts", sa.DateTime, nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("detalle", sa.JSON, nullable=False),
        sa.Column("anterior", sa.String(64), nullable=False),
        sa.Column("sello", sa.String(64), nullable=False, unique=True),
        sa.UniqueConstraint("consentimiento_id", "anterior", name="uq_consentimiento_eventos_cadena"),
    )


def downgrade() -> None:
    op.drop_table("consentimiento_eventos")
    op.drop_table("consentimientos")
