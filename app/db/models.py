import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def now():
    return datetime.now(timezone.utc)


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    platform: Mapped[str] = mapped_column(String(40))
    username: Mapped[str] = mapped_column(String(200))
    profile_url: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    posts: Mapped[list["Post"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="profile")


class Post(Base):
    __tablename__ = "posts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"))
    platform_post_id: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    post_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    profile: Mapped[Profile] = relationship(back_populates="posts")
    claims: Mapped[list["Claim"]] = relationship(back_populates="post", cascade="all, delete-orphan")


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id"))
    text: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(30))
    importance: Mapped[float] = mapped_column(Float)
    verification_status: Mapped[str] = mapped_column(String(40), default="unverified")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    post: Mapped[Post] = relationship(back_populates="claims")
    evidence: Mapped[list["Evidence"]] = relationship(back_populates="claim", cascade="all, delete-orphan")


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"))
    url: Mapped[str] = mapped_column(String(2000))
    title: Mapped[str] = mapped_column(String(1000), default="")
    source_domain: Mapped[str] = mapped_column(String(300), default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    stance: Mapped[str] = mapped_column(String(40), default="context_only")
    source_quality: Mapped[float] = mapped_column(Float, default=0.5)
    relevance: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    claim: Mapped[Claim] = relationship(back_populates="evidence")


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    factuality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sensationalism_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    misleadingness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    correction_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    claims_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile: Mapped[Profile] = relationship(back_populates="analyses")
