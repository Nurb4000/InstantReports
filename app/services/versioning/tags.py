from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import ReportTag


async def add_tag(
    db: AsyncSession,
    report_id: uuid.UUID,
    version_number: int,
    tag_name: str,
    comment: str | None,
    user_id: uuid.UUID,
) -> ReportTag:
    existing = await db.execute(
        select(ReportTag).where(
            ReportTag.report_id == report_id,
            ReportTag.tag_name == tag_name,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"Tag '{tag_name}' already exists on this report")

    tag = ReportTag(
        report_id=report_id,
        version_number=version_number,
        tag_name=tag_name,
        comment=comment,
        created_by=user_id,
    )
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


async def remove_tag(
    db: AsyncSession,
    report_id: uuid.UUID,
    tag_name: str,
) -> bool:
    result = await db.execute(
        select(ReportTag).where(
            ReportTag.report_id == report_id,
            ReportTag.tag_name == tag_name,
        )
    )
    tag = result.scalar_one_or_none()
    if not tag:
        return False

    await db.delete(tag)
    await db.commit()
    return True


async def get_tags(
    db: AsyncSession,
    report_id: uuid.UUID,
) -> list[ReportTag]:
    result = await db.execute(
        select(ReportTag)
        .where(ReportTag.report_id == report_id)
        .order_by(ReportTag.created_at.desc())
    )
    return list(result.scalars().all())
