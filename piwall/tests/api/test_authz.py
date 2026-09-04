import pytest
from fastapi import HTTPException

from backend.main import require_admin


def test_admin_passes():
    require_admin({"id": "p1", "role": "admin"})


def test_non_admin_is_rejected():
    with pytest.raises(HTTPException) as exc:
        require_admin({"id": "p1", "role": "player"})
    assert exc.value.status_code == 403


def test_missing_role_is_rejected():
    with pytest.raises(HTTPException) as exc:
        require_admin({"id": "p1"})
    assert exc.value.status_code == 403
