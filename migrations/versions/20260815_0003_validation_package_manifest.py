"""add canonical immutable validation package manifests

Revision ID: 20260815_0003
Revises: 20260814_0002
Create Date: 2026-08-15
"""

from alembic import op

revision = "20260815_0003"
down_revision = "20260814_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing packages cannot be proven to match the canonical payload that
    # originally produced their hash. Preserve them explicitly as legacy and
    # make every read/promotion path fail closed.
    op.execute("ALTER TABLE validation_packages ADD COLUMN manifest_version TEXT")
    op.execute("ALTER TABLE validation_packages ADD COLUMN canonical_manifest TEXT")
    op.execute("ALTER TABLE validation_packages ADD COLUMN integrity_status TEXT")
    op.execute(
        "UPDATE validation_packages SET integrity_status = 'LEGACY_UNVERIFIABLE'"
    )
    op.execute(
        "ALTER TABLE validation_packages ALTER COLUMN integrity_status SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE validation_packages ADD CONSTRAINT validation_packages_integrity_status_check CHECK (integrity_status IN ('VERIFIED','LEGACY_UNVERIFIABLE'))"
    )
    op.execute(
        "ALTER TABLE validation_packages ADD CONSTRAINT validation_packages_verified_manifest_check CHECK ((integrity_status = 'VERIFIED' AND manifest_version = 'validation-package-manifest-v1' AND canonical_manifest IS NOT NULL AND jsonb_typeof(canonical_manifest::jsonb) = 'object' AND content_hash = encode(digest(convert_to(canonical_manifest, 'UTF8'), 'sha256'), 'hex')) OR (integrity_status = 'LEGACY_UNVERIFIABLE' AND manifest_version IS NULL AND canonical_manifest IS NULL))"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS validation_package_artifacts_immutable ON validation_package_artifacts"
    )
    op.execute(
        "CREATE TRIGGER validation_package_artifacts_immutable BEFORE UPDATE OR DELETE ON validation_package_artifacts FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
    )


def downgrade() -> None:
    # Disposable developer databases only; production evidence is forward-only.
    op.execute(
        "DROP TRIGGER IF EXISTS validation_package_artifacts_immutable ON validation_package_artifacts"
    )
    op.execute(
        "ALTER TABLE validation_packages DROP CONSTRAINT IF EXISTS validation_packages_verified_manifest_check"
    )
    op.execute(
        "ALTER TABLE validation_packages DROP CONSTRAINT IF EXISTS validation_packages_integrity_status_check"
    )
    op.execute(
        "ALTER TABLE validation_packages DROP COLUMN IF EXISTS integrity_status"
    )
    op.execute(
        "ALTER TABLE validation_packages DROP COLUMN IF EXISTS canonical_manifest"
    )
    op.execute(
        "ALTER TABLE validation_packages DROP COLUMN IF EXISTS manifest_version"
    )
