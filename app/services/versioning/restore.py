from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import Report, ReportVersion


async def restore_version(
    db: AsyncSession,
    report_id: uuid.UUID,
    version_number: int,
    user_id: uuid.UUID,
    commit_message: str | None = None,
) -> ReportVersion:
    result = await db.execute(
        select(ReportVersion).where(
            ReportVersion.report_id == report_id,
            ReportVersion.version_number == version_number,
        )
    )
    version = result.scalar_one_or_none()

    if not version:
        raise ValueError(f"Version {version_number} not found")

    report_result = await db.execute(
        select(Report).where(Report.id == report_id)
    )
    report = report_result.scalar_one_or_none()

    if not report:
        raise ValueError("Report not found")

    report.definition = version.definition.copy()
    report.updated_at = __import__("datetime").datetime.utcnow()

    new_version = ReportVersion(
        report_id=report_id,
        version_number=version.version_number + 1,
        definition=version.definition.copy(),
        commit_message=commit_message or f"Restored to v{version_number}",
        created_by=user_id,
    )
    db.add(new_version)
    await db.commit()
    await db.refresh(new_version)
    return new_version
