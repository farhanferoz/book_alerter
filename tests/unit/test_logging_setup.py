import json

from book_alerter.logging_setup import configure_logging, get_logger


def test_logger_emits_json(capsys):
    configure_logging()
    log = get_logger(__name__)
    log.info("hello", isbn="9780000000000", source="test")
    captured = capsys.readouterr()
    last_line = [ln for ln in captured.out.splitlines() if ln.strip()][-1]
    parsed = json.loads(last_line)
    assert parsed["event"] == "hello"
    assert parsed["isbn"] == "9780000000000"
    assert parsed["source"] == "test"
    assert parsed["level"] == "info"
