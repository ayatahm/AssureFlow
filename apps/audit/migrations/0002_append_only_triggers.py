"""Database-level append-only protection (spec 19).

On PostgreSQL a trigger rejects UPDATE and DELETE on audit, snapshot and
history tables, so the guarantee does not rely on application code alone.
Deployments should additionally run the application with a database role that
lacks UPDATE/DELETE privileges on these tables (see docs/deployment.md).
"""
from django.db import migrations

TABLES = (
    "audit_auditevent",
    "reporting_reportsnapshot",
    "assurance_conclusion",
    "assurance_riskrecord",
    "assurance_projectcompliancedecision",
    "assurance_actionhistory",
)

FUNCTION = """
CREATE OR REPLACE FUNCTION assureflow_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'AssureFlow: % on append-only table % is not permitted', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
"""


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(FUNCTION, params=None)
    for table in TABLES:
        schema_editor.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION assureflow_append_only();"
        )


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in TABLES:
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};")
    schema_editor.execute("DROP FUNCTION IF EXISTS assureflow_append_only();")


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
        ("reporting", "0001_initial"),
        ("assurance", "0001_initial"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
