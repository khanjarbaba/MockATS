from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Profile(Base):
    """An ATS Profile: how one ATS (Greenhouse, Workday, ...) talks to SHL."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # slug, e.g. "greenhouse"
    display_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | active
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # actions, auth, webhook contract
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    candidates: Mapped[list["Candidate"]] = relationship(back_populates="profile")


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(256), index=True, default="")
    external_ref: Mapped[str] = mapped_column(String(256), default="")  # requisition/job id etc.
    status: Mapped[str] = mapped_column(String(64), default="created")  # created|invited|started|completed|error
    assessment_link: Mapped[str] = mapped_column(Text, default="")
    results_link: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    profile: Mapped["Profile"] = relationship(back_populates="candidates")


class Transaction(Base):
    """Every HTTP exchange: outbound to SHL, SHL's response, inbound webhooks."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id"), nullable=True, index=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"), nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String(16))  # outbound | webhook
    action: Mapped[str] = mapped_column(String(64), default="")
    method: Mapped[str] = mapped_column(String(8), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    request_headers: Mapped[dict] = mapped_column(JSON, default=dict)
    request_body: Mapped[str] = mapped_column(Text, default="")
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_headers: Mapped[dict] = mapped_column(JSON, default=dict)
    response_body: Mapped[str] = mapped_column(Text, default="")
    dry_run: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookEvent(Base):
    """Parsed interpretation of an inbound SHL webhook."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id"), nullable=True, index=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"), nullable=True, index=True)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(128), default="")
    verified: Mapped[int] = mapped_column(Integer, default=0)  # 1 if signature verified
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    applied_changes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
