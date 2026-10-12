from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class DataImport(Base):
    __tablename__ = "data_imports"

    id = Column(String(36), primary_key=True)
    file_type = Column(String(32), nullable=False)  # "products", "demand", "suppliers"
    filename = Column(String(255), nullable=False)
    status = Column(
        String(32), nullable=False, default="previewed"
    )  # "previewed", "confirmed", "rejected", "failed"
    total_rows = Column(Integer, nullable=False, default=0)
    accepted_rows = Column(Integer, nullable=False, default=0)
    rejected_rows = Column(Integer, nullable=False, default=0)
    warning_rows = Column(Integer, nullable=False, default=0)
    can_confirm = Column(Boolean, nullable=False, default=False)
    staged_data = Column(JSON, nullable=True)
    summary = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    row_errors = relationship(
        "ImportRowError",
        back_populates="data_import",
        cascade="all, delete-orphan",
        order_by="ImportRowError.row_number",
    )

    def __repr__(self) -> str:
        return f"<DataImport(id='{self.id}', file_type='{self.file_type}', status='{self.status}')>"


class ImportRowError(Base):
    __tablename__ = "import_row_errors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_id = Column(
        String(36),
        ForeignKey("data_imports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_number = Column(Integer, nullable=False)
    column_name = Column(String(64), nullable=True)
    error_code = Column(String(64), nullable=False)
    message = Column(String(500), nullable=False)
    is_warning = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    data_import = relationship("DataImport", back_populates="row_errors")

    def __repr__(self) -> str:
        return f"<ImportRowError(import_id='{self.import_id}', row={self.row_number}, code='{self.error_code}')>"
