from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import ReportVersion

_MAX_SAVE_RETRIES = 3


async def save_version(
    db: AsyncSession,
    report_id: uuid.UUID,
    definition: dict,
    commit_message: str,
    user_id: uuid.UUID,
    diff_summary: dict | None = None,
) -> ReportVersion:
    """Persist a new report version.

    The version number is derived from ``max(version_number)+1``. Because the
    ``(report_id, version_number)`` column pair has a unique constraint (M13),
    concurrent saves can collide. We retry up to ``_MAX_SAVE_RETRIES`` times
    on ``IntegrityError`` to resolve the race without holding a row-level lock.
    """
    for attempt in range(_MAX_SAVE_RETRIES):
        result = await db.execute(
            select(func.coalesce(func.max(ReportVersion.version_number), 0))
            .where(ReportVersion.report_id == report_id)
        )
        max_version = result.scalar() or 0

        version = ReportVersion(
            report_id=report_id,
            version_number=max_version + 1,
            definition=definition,
            commit_message=commit_message,
            diff_summary=diff_summary,
            created_by=user_id,
        )
        db.add(version)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            if attempt == _MAX_SAVE_RETRIES - 1:
                raise
        else:
            await db.refresh(version)
            return version

    # Should not reach here, but satisfy type checker.
    result = await db.execute(
        select(func.coalesce(func.max(ReportVersion.version_number), 0))
        .where(ReportVersion.report_id == report_id)
    )
    max_version = result.scalar() or 0
    version = ReportVersion(
        report_id=report_id,
        version_number=max_version + 1,
        definition=definition,
        commit_message=commit_message,
        diff_summary=diff_summary,
        created_by=user_id,
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
    return version


async def get_versions(
    db: AsyncSession,
    report_id: uuid.UUID,
    limit: int = 50,
) -> list[ReportVersion]:
    result = await db.execute(
        select(ReportVersion)
        .where(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version_number.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_version(
    db: AsyncSession,
    report_id: uuid.UUID,
    version_number: int,
) -> ReportVersion | None:
    result = await db.execute(
        select(ReportVersion)
        .where(
            ReportVersion.report_id == report_id,
            ReportVersion.version_number == version_number,
        )
    )
    return result.scalar_one_or_none()


async def get_latest_version(
    db: AsyncSession,
    report_id: uuid.UUID,
) -> ReportVersion | None:
    result = await db.execute(
        select(ReportVersion)
        .where(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
