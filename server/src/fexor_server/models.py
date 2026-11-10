"""Database schema. Raw attendance events are the source of truth; attendance_day is derived."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Role(StrEnum):
    OWNER = "owner"  # everything in the space, incl. admin role assignment
    MANAGER = "manager"  # people, schedule, devices, review
    VIEWER = "viewer"  # read only


ROLE_RANK = {Role.VIEWER: 1, Role.MANAGER: 2, Role.OWNER: 3}


class Mode(StrEnum):
    SINGLE = "single"  # one check per day (school)
    IN_OUT = "in_out"  # check in + check out (office)


class EventStatus(StrEnum):
    ACCEPTED = "accepted"  # counts toward attendance
    REJECTED = "rejected"  # failed verification, kept for audit/analytics
    PENDING = "pending"  # needs admin review, counts only after approval


class DayStatus(StrEnum):
    PRESENT = "present"
    LATE = "late"
    HALF_DAY = "half_day"
    ABSENT = "absent"
    HOLIDAY = "holiday"
    LEAVE = "leave"
    OFF = "off"  # not a scheduled day


# ---------- admin ----------
class AdminUser(Base):
    __tablename__ = "admin_user"
    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret: Mapped[bytes | None] = mapped_column(LargeBinary)  # sealed
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class AdminSession(Base):
    __tablename__ = "admin_session"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_user.id", ondelete="CASCADE"))
    csrf: Mapped[str] = mapped_column(String(64))
    mfa_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))


class SpaceRole(Base):
    __tablename__ = "space_role"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_user.id", ondelete="CASCADE"), primary_key=True
    )
    space_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("space.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20))


class ApiKey(Base):
    """Read-only integration key (reports, events, days)."""

    __tablename__ = "api_key"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    space_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_user.id"))
    created_at: Mapped[datetime] = _now()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------- spaces & people ----------
class Space(Base):
    __tablename__ = "space"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    mode: Mapped[str] = mapped_column(String(10), default=Mode.IN_OUT)
    country: Mapped[str | None] = mapped_column(String(8))  # holiday presets
    # [{key, label, type: text|number|date|select, required, options?}]
    field_schema: Mapped[list[Any]] = mapped_column(default=list)
    member_label: Mapped[str] = mapped_column(String(40), default="Member")
    default_shift_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shift.id", ondelete="SET NULL", use_alter=True)
    )
    min_rescan_minutes: Mapped[int] = mapped_column(Integer, default=2)
    time_format: Mapped[str] = mapped_column(String(3), default="24h")  # "24h" | "12h"
    match_threshold: Mapped[float] = mapped_column(Float, default=0.42)
    liveness_threshold: Mapped[float] = mapped_column(Float, default=0.6)
    challenge_actions: Mapped[int] = mapped_column(Integer, default=2)
    store_failure_snapshots: Mapped[bool] = mapped_column(Boolean, default=False)
    keep_enrollment_photos: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Person(Base):
    """Organization-level identity; may belong to several spaces."""

    __tablename__ = "person"
    id: Mapped[uuid.UUID] = _id()
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (
        UniqueConstraint("space_id", "external_id"),
        UniqueConstraint("space_id", "person_id"),
    )
    id: Mapped[uuid.UUID] = _id()
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(100))  # employee id / roll no
    title: Mapped[str] = mapped_column(String(200), default="")  # designation / class
    fields: Mapped[dict[str, Any]] = mapped_column(default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FaceTemplate(Base):
    __tablename__ = "face_template"
    id: Mapped[uuid.UUID] = _id()
    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[str] = mapped_column(String(40))
    embedding: Mapped[bytes] = mapped_column(LargeBinary)  # sealed, aad=person id
    photo: Mapped[bytes | None] = mapped_column(LargeBinary)  # sealed JPEG
    quality: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = _now()


class QrCredential(Base):
    __tablename__ = "qr_credential"
    id: Mapped[uuid.UUID] = _id()  # = jti in the token
    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), index=True
    )
    key_id: Mapped[str] = mapped_column(String(16))
    issued_at: Mapped[datetime] = _now()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SigningKey(Base):
    __tablename__ = "signing_key"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    private_sealed: Mapped[bytes] = mapped_column(LargeBinary)
    public_b64: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now()


# ---------- schedule ----------
class Shift(Base):
    __tablename__ = "shift"
    id: Mapped[uuid.UUID] = _id()
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)  # earlier than start = overnight shift
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=lambda: [0, 1, 2, 3, 4])
    grace_minutes: Mapped[int] = mapped_column(Integer, default=10)
    early_window_minutes: Mapped[int] = mapped_column(Integer, default=120)
    late_window_minutes: Mapped[int] = mapped_column(Integer, default=240)
    half_day_minutes: Mapped[int | None] = mapped_column(Integer)  # in_out only
    # Enforced windows (None = any time): check-in closes this long after the start,
    # check-out opens this long before the end. Outside them a scan waits for an admin.
    checkin_close_minutes: Mapped[int | None] = mapped_column(Integer)
    checkout_open_minutes: Mapped[int | None] = mapped_column(Integer)  # in_out only
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ShiftAssignment(Base):
    __tablename__ = "shift_assignment"
    id: Mapped[uuid.UUID] = _id()
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("membership.id", ondelete="CASCADE"), index=True
    )
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shift.id", ondelete="CASCADE"))
    valid_from: Mapped[date] = mapped_column(Date)


class Holiday(Base):
    __tablename__ = "holiday"
    __table_args__ = (UniqueConstraint("space_id", "day"),)
    id: Mapped[uuid.UUID] = _id()
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20), default="custom")


class Leave(Base):
    __tablename__ = "leave"
    id: Mapped[uuid.UUID] = _id()
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("membership.id", ondelete="CASCADE"), index=True
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(40), default="leave")
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = _now()


# ---------- devices & attendance ----------
class Device(Base):
    __tablename__ = "device"
    id: Mapped[uuid.UUID] = _id()
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    enroll_code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    enroll_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(64))
    info: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Salted hash of the kiosk's unlock PIN (fexor_core.pin), synced to the kiosk.
    unlock_pin: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class AttendanceEvent(Base):
    __tablename__ = "attendance_event"
    __table_args__ = (
        Index("ix_event_space_time", "space_id", "occurred_at"),
        Index("ix_event_member_time", "membership_id", "occurred_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)  # idempotency
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("membership.id", ondelete="CASCADE")
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device.id", ondelete="SET NULL"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = _now()
    status: Mapped[str] = mapped_column(String(12))
    source: Mapped[str] = mapped_column(String(12), default="kiosk")  # kiosk | admin
    reason: Mapped[str] = mapped_column(String(200), default="")
    scores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    snapshot: Mapped[bytes | None] = mapped_column(LargeBinary)  # sealed JPEG, opt-in
    note: Mapped[str] = mapped_column(String(500), default="")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_user.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttendanceDay(Base):
    __tablename__ = "attendance_day"
    __table_args__ = (Index("ix_day_space_day", "space_id", "day"),)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("membership.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    shift_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shift.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(12))
    first_in: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worked_minutes: Mapped[int] = mapped_column(Integer, default=0)
    late_minutes: Mapped[int] = mapped_column(Integer, default=0)
    early_leave_minutes: Mapped[int] = mapped_column(Integer, default=0)
    overtime_minutes: Mapped[int] = mapped_column(Integer, default=0)
    flags: Mapped[list[Any]] = mapped_column(default=list)
    computed_at: Mapped[datetime] = _now()


# ---------- ops ----------
class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = _now()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_user.id"))
    space_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str] = mapped_column(String(100), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))


class Webhook(Base):
    __tablename__ = "webhook"
    id: Mapped[uuid.UUID] = _id()
    space_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(500))
    secret: Mapped[bytes] = mapped_column(LargeBinary)  # sealed
    events: Mapped[list[Any]] = mapped_column(default=lambda: ["attendance.event"])
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now()


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_due", "done_at", "run_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    run_at: Mapped[datetime] = _now()
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8)
    last_error: Mapped[str | None] = mapped_column(Text)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
