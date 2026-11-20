"""Masque les jetons dans les journaux.

Le WebSocket temps réel reçoit le jeton de session dans l'URL (`?token=…`, les navigateurs
ne permettent pas d'en-tête Authorization sur un WebSocket). Uvicorn journalise cette URL
à chaque connexion : sans ce filtre, le jeton serait lisible dans journald par quiconque
consulte les journaux, et utilisable jusqu'à son expiration.
"""
import logging
import re

_TOKEN_RE = re.compile(r"((?:^|[?&])(?:token|access_token)=)[^&\s\"']+", re.IGNORECASE)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def redact(text: str) -> str:
    return _BEARER_RE.sub(r"\1***", _TOKEN_RE.sub(r"\1***", text))


class TokenRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        return True


def install() -> None:
    """À appeler au chargement de l'application (après la configuration de logging d'Uvicorn)."""
    flt = TokenRedactionFilter()
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn", ""):
        logger = logging.getLogger(name)
        if not any(isinstance(f, TokenRedactionFilter) for f in logger.filters):
            logger.addFilter(flt)
        for handler in logger.handlers:
            if not any(isinstance(f, TokenRedactionFilter) for f in handler.filters):
                handler.addFilter(flt)
