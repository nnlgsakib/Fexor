"""Face enrollment from photos, and QR credentials."""

import io
import re
import uuid
import zipfile
from functools import lru_cache
from typing import Literal

import cv2
import segno
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fexor_core import crypto
from fexor_core.face import MODEL_ID, FaceEngine, decode_image, enroll_from_image, limit_size
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..config import get_settings
from ..db import get_db
from ..models import FaceTemplate, Membership, Role, Space
from ..security import Principal, audit, current, require_space
from ..services import current_credential, issue_credential, token_for

router = APIRouter(prefix="/api", tags=["faces"])
MAX_TEMPLATES = 10


@lru_cache
def engine() -> FaceEngine:
    return FaceEngine()


async def _member(db: AsyncSession, space: Space, member_id: uuid.UUID) -> Membership:
    m = await db.get(Membership, member_id)
    if not m or m.space_id != space.id:
        raise HTTPException(404, "member not found")
    return m


def _analyze(data: bytes):
    img = limit_size(decode_image(data), 1280)
    res = enroll_from_image(engine(), img)
    ok, jpg = cv2.imencode(".jpg", limit_size(img, 640), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return res, jpg.tobytes() if ok else None


async def _enroll(db: AsyncSession, space: Space, m: Membership, filename: str,
                  data: bytes) -> dict:
    try:
        res, jpg = await run_in_threadpool(_analyze, data)
    except ValueError:
        return {"file": filename, "ok": False, "error": "not_an_image"}
    if res.error:
        q = res.quality.__dict__ if res.quality else None
        return {"file": filename, "ok": False, "error": res.error, "quality": q}
    count = len((await db.execute(
        select(FaceTemplate.id).where(FaceTemplate.person_id == m.person_id)
    )).all())
    if count >= MAX_TEMPLATES:
        return {"file": filename, "ok": False, "error": "too_many_photos"}
    key, aad = get_settings().key, str(m.person_id).encode()
    t = FaceTemplate(
        person_id=m.person_id, model_id=MODEL_ID,
        embedding=crypto.seal_vector(key, res.embedding, aad),
        photo=crypto.seal(key, jpg, aad) if (jpg and space.keep_enrollment_photos) else None,
        quality={k: round(float(v), 3) for k, v in res.quality.__dict__.items()
                 if isinstance(v, float)},
    )
    db.add(t)
    await db.flush()
    return {"file": filename, "ok": True, "face_id": str(t.id), "quality": t.quality}


def _check_size(data: bytes) -> None:
    if len(data) > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "file too large")


@router.post("/spaces/{space_id}/members/{member_id}/faces")
async def upload_faces(member_id: uuid.UUID, request: Request,
                       files: list[UploadFile] = File(...),
                       space: Space = Depends(require_space(Role.MANAGER)),
                       p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    m = await _member(db, space, member_id)
    results = []
    for f in files[:MAX_TEMPLATES]:
        data = await f.read()
        _check_size(data)
        results.append(await _enroll(db, space, m, f.filename or "photo", data))
    audit(db, p, "face.enrolled", space_id=space.id, target=m.id, request=request,
          added=sum(r["ok"] for r in results))
    await db.commit()
    return results


@router.post("/spaces/{space_id}/faces/bulk")
async def bulk_faces(request: Request, file: UploadFile = File(...),
                     space: Space = Depends(require_space(Role.MANAGER)),
                     p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """ZIP of photos named by member ID: 1001.jpg, 1001_2.jpg, ..."""
    data = await file.read()
    _check_size(data)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise HTTPException(422, "not a zip file") from e
    members = {m.external_id.lower(): m for m in (await db.execute(
        select(Membership).where(Membership.space_id == space.id)
    )).scalars()}
    results = []
    for info in zf.infolist()[:5000]:
        name = info.filename.rsplit("/", 1)[-1]
        if info.is_dir() or name.startswith(".") or not re.search(r"\.(jpe?g|png|webp)$", name, re.I):
            continue
        if info.file_size > 15 * 1024 * 1024:
            results.append({"file": name, "ok": False, "error": "file_too_large"})
            continue
        ext_id = re.sub(r"(_\d+)?\.[^.]+$", "", name).lower()
        m = members.get(ext_id)
        if not m:
            results.append({"file": name, "ok": False, "error": "no_member_with_this_id"})
            continue
        results.append(await _enroll(db, space, m, name, zf.read(info)))
    audit(db, p, "face.bulk_enrolled", space_id=space.id, request=request,
          added=sum(r["ok"] for r in results), failed=sum(not r["ok"] for r in results))
    await db.commit()
    return results


@router.get("/spaces/{space_id}/members/{member_id}/faces")
async def list_faces(member_id: uuid.UUID, space: Space = Depends(require_space(Role.VIEWER)),
                     db: AsyncSession = Depends(get_db)):
    m = await _member(db, space, member_id)
    rows = (await db.execute(select(FaceTemplate).where(FaceTemplate.person_id == m.person_id)
                             .order_by(FaceTemplate.created_at))).scalars()
    return [{"id": str(t.id), "model_id": t.model_id, "quality": t.quality,
             "has_photo": t.photo is not None, "created_at": t.created_at} for t in rows]


async def _template(db: AsyncSession, m: Membership, face_id: uuid.UUID) -> FaceTemplate:
    t = await db.get(FaceTemplate, face_id)
    if not t or t.person_id != m.person_id:
        raise HTTPException(404, "face not found")
    return t


@router.get("/spaces/{space_id}/members/{member_id}/faces/{face_id}/photo")
async def face_photo(member_id: uuid.UUID, face_id: uuid.UUID, request: Request,
                     space: Space = Depends(require_space(Role.MANAGER)),
                     p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    m = await _member(db, space, member_id)
    t = await _template(db, m, face_id)
    if not t.photo:
        raise HTTPException(404, "photo not kept")
    audit(db, p, "face.photo_viewed", space_id=space.id, target=t.id, request=request)
    await db.commit()
    jpg = crypto.unseal(get_settings().key, t.photo, str(m.person_id).encode())
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.delete("/spaces/{space_id}/members/{member_id}/faces/{face_id}")
async def delete_face(member_id: uuid.UUID, face_id: uuid.UUID, request: Request,
                      space: Space = Depends(require_space(Role.MANAGER)),
                      p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    m = await _member(db, space, member_id)
    await db.delete(await _template(db, m, face_id))
    audit(db, p, "face.deleted", space_id=space.id, target=face_id, request=request)
    await db.commit()
    return {"ok": True}


# ---------- QR ----------
@router.post("/spaces/{space_id}/members/{member_id}/qr")
async def reissue_qr(member_id: uuid.UUID, request: Request,
                     space: Space = Depends(require_space(Role.MANAGER)),
                     p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """New QR; any previously printed QR for this person stops working on next kiosk sync."""
    m = await _member(db, space, member_id)
    cred = await issue_credential(db, m.person_id)
    audit(db, p, "qr.issued", space_id=space.id, target=m.id, request=request)
    await db.commit()
    return {"credential_id": str(cred.id), "issued_at": cred.issued_at}


@router.get("/spaces/{space_id}/members/{member_id}/qr.{fmt}")
async def qr_image(member_id: uuid.UUID, fmt: Literal["svg", "png"],
                   space: Space = Depends(require_space(Role.MANAGER)),
                   db: AsyncSession = Depends(get_db)):
    m = await _member(db, space, member_id)
    cred = await current_credential(db, m.person_id)
    if cred is None:
        cred = await issue_credential(db, m.person_id)
        await db.commit()
    token = await token_for(db, cred)
    # Standalone SVG (with xmlns): browsers won't render an <img> SVG without it.
    buf = io.BytesIO()
    segno.make(token, error="m").save(buf, kind=fmt, scale=6 if fmt == "svg" else 10, border=4)
    headers = {"Cache-Control": "no-store"}
    if fmt == "png":
        headers["Content-Disposition"] = f'attachment; filename="qr-{m.external_id}.png"'
    return Response(buf.getvalue(), media_type="image/svg+xml" if fmt == "svg" else "image/png",
                    headers=headers)
