from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Базовый класс декларативных моделей SQLAlchemy."""


class DownloadedFile(Base):
    """Метаданные о скачанном с сервера файле."""

    __tablename__ = "downloaded_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    marked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
