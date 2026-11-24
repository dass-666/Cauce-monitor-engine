"""Observador universal · una fuente de observación puede ser una URL configurable (sin código por proveedor)

- observadores_config.url           dirección de la página a observar (NULL: el calendario del portal de laboratorio)
- observadores_config.perfil        perfil de extracción (selectores, reglas, ruido a ignorar, equivalencias de ubicación)
- observadores_config.autorizacion  autorización escrita para observar un sitio que no es local (ref, sha256, vigente_hasta)
Sin datos personales. Las filas existentes no cambian (columnas NULL = comportamiento anterior).

Revision ID: 0018_fuentes_web
Revises: 0017_asistido
"""
import sqlalchemy as sa
from alembic import op

revision = "0018_fuentes_web"
down_revision = "0017_asistido"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("observadores_config") as b:
        b.add_column(sa.Column("url", sa.String(300), nullable=True))
        b.add_column(sa.Column("perfil", sa.JSON, nullable=True))
        b.add_column(sa.Column("autorizacion", sa.JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("observadores_config") as b:
        b.drop_column("autorizacion")
        b.drop_column("perfil")
        b.drop_column("url")
