"""Unit tests for versioning service."""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import Report, ReportVersion
from app.models.user import User, UserRole, AuthSource
from app.services.versioning import save_version, get_versions, get_version, restore_version
from app.services.versioning.diff import ReportDiffEngine


class TestVersioningService:
    """Test versioning service."""

    @pytest.mark.asyncio
    async def test_save_version(self, db_session: AsyncSession, test_user: User):
        """Should save a new version."""
        definition = {"name": "Test Report", "layout": {"sections": []}}
        
        version = await save_version(
            db=db_session,
            report_id=test_user.id,  # Using user.id as report_id for test
            definition=definition,
            commit_message="Initial version",
            user_id=test_user.id,
        )
        
        assert version.version_number == 1
        assert version.commit_message == "Initial version"

    @pytest.mark.asyncio
    async def test_get_versions(self, db_session: AsyncSession, test_user: User):
        """Should retrieve all versions for a report."""
        # Save multiple versions
        for i in range(3):
            await save_version(
                db=db_session,
                report_id=test_user.id,
                definition={"version": i},
                commit_message=f"Version {i}",
                user_id=test_user.id,
            )
        
        versions = await get_versions(db_session, test_user.id)
        
        assert len(versions) == 3

    @pytest.mark.asyncio
    async def test_get_version(self, db_session: AsyncSession, test_user: User):
        """Should retrieve a specific version."""
        await save_version(
            db=db_session,
            report_id=test_user.id,
            definition={"test": True},
            commit_message="Test",
            user_id=test_user.id,
        )
        
        version = await get_version(db_session, test_user.id, 1)
        
        assert version is not None
        assert version.version_number == 1

    @pytest.mark.asyncio
    async def test_restore_version(self, db_session: AsyncSession, test_user: User):
        """Should restore a previous version."""
        # Save two versions
        await save_version(
            db=db_session,
            report_id=test_user.id,
            definition={"data": "v1"},
            commit_message="Version 1",
            user_id=test_user.id,
        )
        await save_version(
            db=db_session,
            report_id=test_user.id,
            definition={"data": "v2"},
            commit_message="Version 2",
            user_id=test_user.id,
        )
        
        # Restore version 1
        new_version = await restore_version(
            db=db_session,
            report_id=test_user.id,
            version_number=1,
            user_id=test_user.id,
        )
        
        assert new_version.version_number == 3  # Next version after 2


class TestReportDiffEngine:
    """Test semantic diff engine."""

    def test_diff_empty_reports(self):
        """Should return empty diff for identical reports."""
        engine = ReportDiffEngine()
        definition = {"layout": {"sections": []}}
        
        diff = engine.diff(definition, definition)
        
        assert diff["sections_added"] == []
        assert diff["sections_removed"] == []
        assert diff["sections_modified"] == []

    def test_diff_added_section(self):
        """Should detect added sections."""
        engine = ReportDiffEngine()
        old_def = {"layout": {"sections": []}}
        new_def = {"layout": {"sections": [{"type": "header"}]}}
        
        diff = engine.diff(old_def, new_def)
        
        assert "header" in diff["sections_added"]

    def test_diff_removed_section(self):
        """Should detect removed sections."""
        engine = ReportDiffEngine()
        old_def = {"layout": {"sections": [{"type": "header"}]}}
        new_def = {"layout": {"sections": []}}
        
        diff = engine.diff(old_def, new_def)
        
        assert "header" in diff["sections_removed"]

    def test_diff_modified_element(self):
        """Should detect modified elements."""
        engine = ReportDiffEngine()
        old_def = {
            "layout": {
                "sections": [
                    {
                        "type": "detail",
                        "elements": [
                            {"type": "table", "sort": "name ASC"}
                        ]
                    }
                ]
            }
        }
        new_def = {
            "layout": {
                "sections": [
                    {
                        "type": "detail",
                        "elements": [
                            {"type": "table", "sort": "name DESC"}
                        ]
                    }
                ]
            }
        }
        
        diff = engine.diff(old_def, new_def)
        
        assert len(diff["sections_modified"]) > 0
