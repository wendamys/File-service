from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.logger import get_logger
from app.models import Base, DownloadedFile
from app.timeutils import utcnow

logger = get_logger(__name__)


class Storage:
    """Доступ к БД метаданных скачанных файлов."""

    def __init__(self, db_url: str):
        self.engine: Engine = create_engine(db_url, connect_args={"check_same_thread": False})
        if self.engine.url.get_backend_name() == "sqlite":
            @event.listens_for(self.engine, "connect")
            def _set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

    def init_db(self) -> None:
        Base.metadata.create_all(self.engine)

    def add_file(
        self,
        name: str,
        size_bytes: int,
        marked: bool = False,
        downloaded_at: datetime | None = None,
    ) -> None:
        """Добавить запись о файле либо обновить существующую, не трогая downloaded_at."""
        with Session(self.engine) as session:
            existing = session.scalar(select(DownloadedFile).where(DownloadedFile.name == name))
            if existing is None:
                session.add(DownloadedFile(
                    name=name,
                    downloaded_at=downloaded_at or utcnow(),
                    size_bytes=size_bytes,
                    marked=marked,
                ))
            else:
                existing.size_bytes = size_bytes
                # Повторный add_file(marked=False) не должен снимать отметку.
                existing.marked = existing.marked or marked
            session.commit()

    def mark_files(self, names: list[str]) -> None:
        if not names:
            return
        with Session(self.engine) as session:
            rows = session.scalars(select(DownloadedFile).where(DownloadedFile.name.in_(names))).all()
            for row in rows:
                row.marked = True
            session.commit()

    def known_names(self) -> set[str]:
        with Session(self.engine) as session:
            return set(session.scalars(select(DownloadedFile.name)).all())

    def all_names(self) -> list[str]:
        with Session(self.engine) as session:
            return list(session.scalars(select(DownloadedFile.name)).all())

    def unmarked_names(self) -> list[str]:
        """Имена скачанных, но ещё не отмеченных на сервере файлов."""
        with Session(self.engine) as session:
            return list(session.scalars(
                select(DownloadedFile.name).where(DownloadedFile.marked.is_(False))
            ).all())

    def list_files(self, page: int, per_page: int, sort: str) -> tuple[list[DownloadedFile], int]:
        """Страница файлов, отсортированных по downloaded_at, и общее количество."""
        if sort not in ("asc", "desc"):
            raise ValueError(f"Недопустимое значение sort: {sort!r}")
        order = (
            DownloadedFile.downloaded_at.asc()
            if sort == "asc"
            else DownloadedFile.downloaded_at.desc()
        )

        with Session(self.engine) as session:
            total = session.scalar(select(func.count()).select_from(DownloadedFile)) or 0
            rows = session.scalars(
                select(DownloadedFile)
                .order_by(order)
                .offset((page - 1) * per_page)
                .limit(per_page)
            ).all()
            return list(rows), total

    def count(self) -> int:
        with Session(self.engine) as session:
            return session.scalar(select(func.count()).select_from(DownloadedFile)) or 0

    def backfill_from_disk(self, downloads_dir: Path) -> int:
        """Добавить в БД файлы, которые есть на диске, но потерялись в таблице."""
        known = self.known_names()
        added = 0
        for path in sorted(downloads_dir.glob("*.txt")):
            if path.name in known:
                continue
            stat = path.stat()
            self.add_file(
                name=path.name,
                size_bytes=stat.st_size,
                marked=True,
                downloaded_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            )
            added += 1
        if added:
            logger.info("Добавлено в БД %s файлов, найденных на диске", added)
        return added

    def prune_missing(self, downloads_dir: Path) -> int:
        """Удалить записи о файлах, которых больше нет на диске.

        Иначе удалённые вручную файлы навсегда остаются в выдаче и ломают
        расчёты. Неотмеченные записи после удаления снова придут в списке имён
        от сервера и будут перекачаны.
        """
        if not downloads_dir.is_dir():
            logger.warning("Директория %s недоступна — сверка с диском пропущена", downloads_dir)
            return 0

        on_disk = {path.name for path in downloads_dir.glob("*.txt")}
        with Session(self.engine) as session:
            rows = session.scalars(select(DownloadedFile)).all()
            stale = [row for row in rows if row.name not in on_disk]
            for row in stale:
                session.delete(row)
            session.commit()

        if stale:
            logger.info("Удалено %s записей о файлах, пропавших с диска", len(stale))
        return len(stale)
