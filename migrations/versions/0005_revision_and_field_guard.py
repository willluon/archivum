"""Aggregate revision counter + metadata_fields structural guard (ADR-0010).

entries.revision backs HTTP ETags/If-Match; the trigger closes the V0.3
gap where field structural immutability after publication was service-only
— the composite FKs of ADR-0008 are sound only if key/field_type/schema_id
never change once a schema leaves draft.

Downgrade is honestly reversible: revision is concurrency state (resetting
it loses no document data) and the trigger is a pure guard.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-12

"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entries",
        sa.Column("revision", sa.BigInteger, nullable=False, server_default=sa.text("1")),
    )
    op.create_check_constraint("ck_entries_revision_positive", "entries", "revision >= 1")

    op.execute(
        """
        CREATE FUNCTION metadata_fields_guard() RETURNS trigger AS $$
        DECLARE
            sid uuid;
            sstate text;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.key = OLD.key AND NEW.field_type = OLD.field_type
                   AND NEW.schema_id = OLD.schema_id AND NEW.required = OLD.required THEN
                    RETURN NEW;  -- display-only change (label/position/description)
                END IF;
                sid := OLD.schema_id;
            ELSIF TG_OP = 'DELETE' THEN
                sid := OLD.schema_id;
            ELSE
                sid := NEW.schema_id;
            END IF;
            SELECT state INTO sstate FROM metadata_schemas WHERE id = sid;
            IF sstate IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION
                    'metadata_fields structural change requires a draft schema (schema % is %)',
                    sid, sstate;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER metadata_fields_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON metadata_fields FOR EACH ROW EXECUTE FUNCTION metadata_fields_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER metadata_fields_guard ON metadata_fields")
    op.execute("DROP FUNCTION metadata_fields_guard()")
    op.drop_constraint("ck_entries_revision_positive", "entries", type_="check")
    op.drop_column("entries", "revision")
