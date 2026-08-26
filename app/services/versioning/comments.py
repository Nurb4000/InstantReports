from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import ReportComment


async def add_comment(
    db: AsyncSession,
    report_id: uuid.UUID,
    version_number: int,
    comment_text: str,
    user_id: uuid.UUID,
) -> ReportComment:
    comment = ReportComment(
        report_id=report_id,
        version_number=version_number,
        comment_text=comment_text,
        created_by=user_id,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


async def get_comments(
    db: AsyncSession,
    report_id: uuid.UUID,
    version_number: int | None = None,
) -> list[ReportComment]:
    query = select(ReportComment).where(ReportComment.report_id == report_id)
    if version_number is not None:
        query = query.where(ReportComment.version_number == version_number)
    query = query.order_by(ReportComment.created_at.desc())

    result = await db.execute(query)
    return list(result.scalars().all())


async def delete_comment(
    db: AsyncSession,
    comment_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(ReportComment).where(
            ReportComment.id == comment_id,
            ReportComment.created_by == user_id,
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        return False

    await db.delete(comment)
    await db.commit()
    return True
