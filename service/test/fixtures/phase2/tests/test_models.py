from data.models import User


def test_user_query():
    return session.query(User)
