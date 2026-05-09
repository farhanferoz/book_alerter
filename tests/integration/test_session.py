from sqlalchemy import text

from book_alerter.db.session import get_engine, session_scope


def test_session_can_execute_simple_query(tmp_path):
    db_path = tmp_path / "t.db"
    engine = get_engine(f"sqlite:///{db_path}")
    with session_scope(engine) as session:
        result = session.execute(text("SELECT 1")).one()
        assert result[0] == 1
