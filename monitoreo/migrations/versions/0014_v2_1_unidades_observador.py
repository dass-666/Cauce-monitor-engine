"""v2.1 · unidades por día observado, configuración persistente del observador

Revision ID: 0014_v2_1
Revises: 0013_v2_1
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_v2_1"
down_revision = "0013_v2_1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("observacion_dias") as b:
        b.add_column(sa.Column("libres", sa.Integer, nullable=True))
    with op.batch_alter_table("observacion_eventos") as b:
        b.add_column(sa.Column("unidades", sa.Integer, nullable=True))
        b.add_column(sa.Column("unidades_anteriores", sa.Integer, nullable=True))
    op.create_table(
        "observadores_config",
        sa.Column("clave", sa.String(96), primary_key=True),
        sa.Column("sucursal", sa.String(32), nullable=False),
        sa.Column("tramite", sa.String(32), nullable=False),
        sa.Column("mes", sa.String(7), nullable=True),
        sa.Column("estrategia", sa.String(8), nullable=False),
        sa.Column("intervalo_visual_ms", sa.Integer, nullable=False),
        sa.Column("activo", sa.Boolean, nullable=False),
        sa.Column("estado", sa.String(24), nullable=False),
        sa.Column("motivo", sa.String(200), nullable=False),
        sa.Column("titular", sa.String(128), nullable=True),
        sa.Column("reinicios", sa.Integer, nullable=False),
        sa.Column("creado_por", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("observadores_config")
    with op.batch_alter_table("observacion_eventos") as b:
        b.drop_column("unidades_anteriores")
        b.drop_column("unidades")
    with op.batch_alter_table("observacion_dias") as b:
        b.drop_column("libres")
