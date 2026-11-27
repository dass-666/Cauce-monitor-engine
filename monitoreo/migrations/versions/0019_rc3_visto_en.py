"""RC3 · M3 · cuándo vio el observador la página por última vez, aunque no cambiara

- observacion_dias.visto_en   última lectura de ese día (con o sin cambio). NULL en filas anteriores (= actualizado_en).
Sirve para no contar dos veces una reservación ya confirmada que la página YA descontó (E7, hallazgo M3).
Sin datos personales.

Revision ID: 0019_rc3_visto_en
Revises: 0018_fuentes_web
"""
import sqlalchemy as sa
from alembic import op

revision = "0019_rc3_visto_en"
down_revision = "0018_fuentes_web"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("observacion_dias") as b:
        b.add_column(sa.Column("visto_en", sa.DateTime, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("observacion_dias") as b:
        b.drop_column("visto_en")
