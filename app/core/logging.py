import logging
import re
import sys

# Regex patterns to detect and mask sensitive information in log streams
SENSITIVE_PATTERNS = [
    (re.compile(r"(bearer\s+)[A-Za-z0-9_\-\.=]+", re.IGNORECASE), r"\1[MASKED_TOKEN]"),
    (
        re.compile(r"(password['\":\s=]+)(['\"][^'\"]+['\"]|[^\s,&]+)", re.IGNORECASE),
        r"\1[MASKED_PASSWORD]",
    ),
    (
        re.compile(r"(secret['\":\s=]+)(['\"][^'\"]+['\"]|[^\s,&]+)", re.IGNORECASE),
        r"\1[MASKED_SECRET]",
    ),
    (
        re.compile(r"(token['\":\s=]+)(['\"][^'\"]+['\"]|[^\s,&]+)", re.IGNORECASE),
        r"\1[MASKED_TOKEN]",
    ),
    (
        re.compile(r"(postgres(?:ql)?:\/\/[^:]+:)([^@]+)(@)", re.IGNORECASE),
        r"\1[MASKED_PASSWORD]\3",
    ),
]


class SensitiveDataFilter(logging.Filter):
    """
    Log filter that masks authentication tokens, passwords, and connection secrets.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern, replacement in SENSITIVE_PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        if record.args:
            if isinstance(record.args, dict):
                sanitized_args = {}
                for k, v in record.args.items():
                    if isinstance(v, str):
                        for pattern, replacement in SENSITIVE_PATTERNS:
                            v = pattern.sub(replacement, v)
                    sanitized_args[k] = v
                record.args = sanitized_args
            elif isinstance(record.args, tuple):
                sanitized_args_list = []
                for v in record.args:
                    if isinstance(v, str):
                        for pattern, replacement in SENSITIVE_PATTERNS:
                            v = pattern.sub(replacement, v)
                    sanitized_args_list.append(v)
                record.args = tuple(sanitized_args_list)
        return True


def setup_logging(log_level: str = "INFO") -> None:
    """
    Configure application-wide structured logging with sensitive data masking.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Avoid duplicate handlers on reload
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.addFilter(SensitiveDataFilter())
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setLevel(level)
            handler.addFilter(SensitiveDataFilter())

    # Quiet overly chatty third-party loggers if needed
    logging.getLogger("uvicorn.access").setLevel(level)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.WARNING if level > logging.DEBUG else logging.INFO
    )


logger = logging.getLogger("stocksense")
