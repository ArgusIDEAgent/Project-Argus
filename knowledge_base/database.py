"""SQLite connections that keep transient query/sort contents in memory."""
import sqlite3


def connect(*args, **kwargs):
    db = sqlite3.connect(*args, **kwargs)
    try:
        db.execute('PRAGMA temp_store=MEMORY')
    except BaseException:
        db.close()
        raise
    return db
