"""One definition of where the database is.

23b4b5b added db.models.mongo_url() and routed the worker and the health
probe through it -- and left a byte-for-byte duplicate of its body in
main.py, plus a third, DIFFERENT precedence rule inside create_db_engine's
own no-arg fallback (MONGODB_URI but not DATABASE_URL). Three rules that
agree today can stop agreeing, and the symptom is silent: one process
writing to a database another one reads.
"""

import backend.db.models as models


def test_main_does_not_redefine_the_connection_string(monkeypatch):
    """Red line: `DB_URL = mongo_url()` in main.py. Paste the old
    `os.environ.get("MONGODB_URI") or ...` expression back and this stays
    green only until someone edits one of the two -- so it is asserted as
    provenance, not as equality of today's values.
    """
    import ast
    import pathlib

    source = pathlib.Path(models.__file__).parent.parent / "main.py"
    tree = ast.parse(source.read_text())
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "DB_URL" for t in node.targets)
    ]
    assert len(assignments) == 1, "DB_URL must be assigned exactly once"
    value = assignments[0].value
    assert isinstance(value, ast.Call) and value.func.id == "mongo_url", (
        "main.DB_URL must come from db.models.mongo_url(), not from a "
        "second copy of its precedence rule"
    )


def test_database_url_is_honoured_by_the_no_argument_fallback(monkeypatch):
    """create_db_engine()'s own fallback read MONGODB_URI but not
    DATABASE_URL, so a deployment that sets only DATABASE_URL got the real
    server from some call sites and localhost from others.

    Red line: `resolved = url or mongo_url()` in models.create_db_engine.
    Put `os.environ.get("MONGODB_URI") or "mongodb://127.0.0.1:27017/phi1"`
    back and this goes red.
    """
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGODB_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "mongodb://198.51.100.9:27017/elsewhere")

    captured = {}

    class FakeClient:
        def __init__(self, url):
            captured["url"] = url

        def __getitem__(self, name):
            captured["database"] = name
            return object()

    monkeypatch.setattr(models, "MongoClient", FakeClient)
    models.create_db_engine()
    assert captured["url"] == "mongodb://198.51.100.9:27017/elsewhere"
    assert captured["database"] == "elsewhere"


def test_mongodb_uri_still_wins_over_database_url(monkeypatch):
    """The precedence itself, so unifying the rule did not quietly change
    it."""
    monkeypatch.setenv("MONGODB_URI", "mongodb://a/one")
    monkeypatch.setenv("DATABASE_URL", "mongodb://b/two")
    assert models.mongo_url() == "mongodb://a/one"
