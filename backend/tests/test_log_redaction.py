"""Les jetons de session ne doivent jamais apparaître dans les journaux."""
import logging

from app.core.log_redaction import TokenRedactionFilter, redact

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"


def test_redact_query_and_bearer():
    assert redact(f"/api/ws/updates?token={JWT}") == "/api/ws/updates?token=***"
    assert redact(f"/x?a=1&token={JWT}&b=2") == "/x?a=1&token=***&b=2"
    assert redact(f"Authorization: Bearer {JWT}") == "Authorization: Bearer ***"
    assert redact("/api/metrics/realtime?with_sparkline=true") == "/api/metrics/realtime?with_sparkline=true"


def test_uvicorn_websocket_log_line_is_redacted(caplog):
    logger = logging.getLogger("uvicorn.error.test")
    logger.addFilter(TokenRedactionFilter())
    with caplog.at_level(logging.INFO):
        # Format exact utilisé par Uvicorn pour une connexion WebSocket acceptée.
        logger.info('%s - "WebSocket %s" [accepted]', "127.0.0.1:5000", f"/api/ws/updates?token={JWT}")
    assert JWT not in caplog.text and "token=***" in caplog.text
