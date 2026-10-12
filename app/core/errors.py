import logging
from typing import List, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("stocksense.errors")


class ErrorDetail(BaseModel):
    field: Optional[str] = None
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Optional[List[ErrorDetail]] = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class AppException(Exception):
    """Base application exception for controlled business/system errors."""

    def __init__(
        self,
        message: str,
        code: str = "APPLICATION_ERROR",
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Optional[List[ErrorDetail]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details


class DatabaseUnavailableException(AppException):
    """Raised when the database cannot be reached."""

    def __init__(self, message: str = "Database connection is unavailable"):
        super().__init__(
            message=message,
            code="DATABASE_UNAVAILABLE",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def register_error_handlers(app: FastAPI) -> None:
    """Register uniform global exception handlers on the FastAPI application."""

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        logger.warning("Application exception (%s): %s", exc.code, exc.message)
        payload = ErrorResponse(
            error=ErrorBody(
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=payload.model_dump(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code_name = f"HTTP_{exc.status_code}"
        message = str(exc.detail) if exc.detail else "An HTTP error occurred"
        if isinstance(exc.detail, dict):
            code_name = str(exc.detail.get("code", code_name))
            message = str(exc.detail.get("message", message))
        payload = ErrorResponse(
            error=ErrorBody(
                code=code_name,
                message=message,
            )
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=payload.model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details: List[ErrorDetail] = []
        for err in exc.errors():
            loc = " -> ".join(str(item) for item in err.get("loc", []))
            details.append(ErrorDetail(field=loc or None, message=err.get("msg", "Invalid value")))

        payload = ErrorResponse(
            error=ErrorBody(
                code="VALIDATION_ERROR",
                message="Request validation failed",
                details=details,
            )
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=payload.model_dump(),
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        # Log internal database error securely on server side
        logger.error("Internal database error: %s", str(exc), exc_info=True)
        # Controlled public response without leaking DB schema or stack trace
        payload = ErrorResponse(
            error=ErrorBody(
                code="DATABASE_ERROR",
                message="A database error occurred. Please try again later.",
            )
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=payload.model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Log full traceback securely on server side
        logger.error("Unhandled server exception: %s", str(exc), exc_info=True)
        # Safe generic response to public clients
        payload = ErrorResponse(
            error=ErrorBody(
                code="INTERNAL_SERVER_ERROR",
                message="An unexpected internal server error occurred.",
            )
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=payload.model_dump(),
        )
