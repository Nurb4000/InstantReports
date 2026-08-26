from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.routes.auth import get_current_user_optional
from app.database import get_db
from app.models.report import Report, ReportVersion
from app.models.user import User
from app.services.versioning import (
    add_comment,
    add_tag,
    delete_comment,
    get_comments,
    get_tags,
    get_versions,
    remove_tag,
    restore_version,
    save_version,
)
from app.services.versioning.diff import ReportDiffEngine

router = APIRouter()

diff_engine = ReportDiffEngine()


@router.get("/{report_id}/versions")
async def list_versions(
    report_id: uuid.UUID,
    limit: int = 50,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    versions = await get_versions(db, report_id, limit)
    return [
        {
            "id": str(v.id),
            "version_number": v.version_number,
            "commit_message": v.commit_message,
            "created_by": str(v.created_by),
            "created_at": v.created_at.isoformat(),
        }
        for v in versions
    ]


@router.get("/{report_id}/versions/{version_number}")
async def get_version_detail(
    report_id: uuid.UUID,
    version_number: int,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    version = await __import__("app.services.versioning.store").get_version(
        db, report_id, version_number
    )
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    return {
        "id": str(version.id),
        "version_number": version.version_number,
        "definition": version.definition,
        "commit_message": version.commit_message,
        "diff_summary": version.diff_summary,
        "created_by": str(version.created_by),
        "created_at": version.created_at.isoformat(),
    }


@router.post("/{report_id}/versions")
async def create_version(
    report_id: uuid.UUID,
    request: Request,
    commit_message: str = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or current_user.role.value not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    form = await request.form()
    definition = __import__("json").loads(form.get("definition", "{}"))

    report_result = await db.execute(
        __import__("sqlalchemy").select(Report).where(Report.id == report_id)
    )
    report = report_result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    latest_version = await __import__("app.services.versioning.store").get_latest_version(db, report_id)
    diff_summary = None
    if latest_version:
        diff_summary = diff_engine.diff(latest_version.definition, definition)

    version = await save_version(
        db=db,
        report_id=report_id,
        definition=definition,
        commit_message=commit_message or form.get("commit_message", "Manual save"),
        user_id=current_user.id,
        diff_summary=diff_summary,
    )

    report.definition = definition
    report.updated_at = __import__("datetime").datetime.utcnow()
    await db.commit()

    return {
        "id": str(version.id),
        "version_number": version.version_number,
        "commit_message": version.commit_message,
    }


@router.post("/{report_id}/versions/{version_number}/restore")
async def restore_version_endpoint(
    report_id: uuid.UUID,
    version_number: int,
    commit_message: str = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or current_user.role.value not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        new_version = await restore_version(
            db=db,
            report_id=report_id,
            version_number=version_number,
            user_id=current_user.id,
            commit_message=commit_message,
        )
        return {"status": "ok", "new_version": new_version.version_number}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{report_id}/versions/{version_number}/tags")
async def tag_version(
    report_id: uuid.UUID,
    version_number: int,
    tag_name: str,
    comment: str = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or current_user.role.value not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        tag = await add_tag(
            db=db,
            report_id=report_id,
            version_number=version_number,
            tag_name=tag_name,
            comment=comment,
            user_id=current_user.id,
        )
        return {"status": "ok", "tag": str(tag.tag_name)}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/{report_id}/tags/{tag_name}")
async def untag_version(
    report_id: uuid.UUID,
    tag_name: str,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or current_user.role.value not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    success = await remove_tag(db, report_id, tag_name)
    if not success:
        raise HTTPException(status_code=404, detail="Tag not found")

    return {"status": "ok"}


@router.get("/{report_id}/versions/{version_number}/comments")
async def list_comments(
    report_id: uuid.UUID,
    version_number: int,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    comments = await get_comments(db, report_id, version_number)
    return [
        {
            "id": str(c.id),
            "comment_text": c.comment_text,
            "created_by": str(c.created_by),
            "created_at": c.created_at.isoformat(),
        }
        for c in comments
    ]


@router.post("/{report_id}/versions/{version_number}/comments")
async def add_comment_endpoint(
    report_id: uuid.UUID,
    version_number: int,
    comment_text: str,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    comment = await add_comment(
        db=db,
        report_id=report_id,
        version_number=version_number,
        comment_text=comment_text,
        user_id=current_user.id,
    )
    return {"status": "ok", "id": str(comment.id)}


@router.delete("/comments/{comment_id}")
async def delete_comment_endpoint(
    comment_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    success = await delete_comment(db, comment_id, current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Comment not found or not authorized")

    return {"status": "ok"}


@router.get("/{report_id}/versions/{v1}/diff/{v2}")
async def diff_versions(
    report_id: uuid.UUID,
    v1: int,
    v2: int,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    version1 = await __import__("app.services.versioning.store").get_version(db, report_id, v1)
    version2 = await __import__("app.services.versioning.store").get_version(db, report_id, v2)

    if not version1 or not version2:
        raise HTTPException(status_code=404, detail="Version not found")

    diff = diff_engine.diff(version1.definition, version2.definition)
    return diff
