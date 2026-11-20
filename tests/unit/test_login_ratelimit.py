"""Login rate limiting behaviour."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import status


@pytest.mark.asyncio
async def test_login_rate_limit_blocks_after_max_attempts():
    from app.routes import auth

    app = MagicMock()
    app.state.templates.TemplateResponse = MagicMock(return_value="tpl")

    # Fixed clock for deterministic retries
    t = [0.0]

    def clock():
        return t[0]

    limiter = auth.RateLimiter(max_attempts=2, window_seconds=10, clock=clock)
    auth._login_rate_limiter = limiter

    class DummyReq:
        def __init__(self):
            self.client = MagicMock(host="1.2.3.4")
            self.headers = {}
            self.app = app

    req = DummyReq()

    # First failed attempt -> recorded, returns 401 template
    app.state.templates.TemplateResponse.reset_mock()
    with pytest.MonkeyPatch().context() as m:
        async def no_user(*a, **kw):
            return None
        m.setattr(auth, "authenticate_user", no_user)
        m.setattr(auth, "authenticate_ldap_user", no_user)
        resp = await auth.login(req, email="user@test.com", password="bad")
    assert resp == "tpl"
    app.state.templates.TemplateResponse.assert_called()

    # Second failed attempt -> still within window (count==2), next should be blocked
    app.state.templates.TemplateResponse.reset_mock()
    with pytest.MonkeyPatch().context() as m:
        async def no_user(*a, **kw):
            return None
        m.setattr(auth, "authenticate_user", no_user)
        m.setattr(auth, "authenticate_ldap_user", no_user)
        resp = await auth.login(req, email="user@test.com", password="bad2")
    assert resp == "tpl"

    # Third -> rate limited (429)
    with pytest.MonkeyPatch().context() as m:
        async def no_user(*a, **kw):
            return None
        m.setattr(auth, "authenticate_user", no_user)
        m.setattr(auth, "authenticate_ldap_user", no_user)
        resp = await auth.login(req, email="user@test.com", password="bad3")
    assert resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    # After window passes, it should allow again
    t[0] = 20.0
    app.state.templates.TemplateResponse.reset_mock()
    with pytest.MonkeyPatch().context() as m:
        async def no_user(*a, **kw):
            return None
        m.setattr(auth, "authenticate_user", no_user)
        m.setattr(auth, "authenticate_ldap_user", no_user)
        resp = await auth.login(req, email="user@test.com", password="bad4")
    assert resp == "tpl"


@pytest.mark.asyncio
async def test_login_ldap_backend_error_returns_uniform_401_not_500():
    """A broken LDAP backend (LDAPConfigError) must surface as the same uniform
    "incorrect email or password" 401 as any other failed login -- not a 500 --
    so it does not reveal backend state (enumeration) and is still rate-limited."""
    from app.routes import auth

    app = MagicMock()
    app.state.templates.TemplateResponse = MagicMock(return_value="tpl")

    limiter = auth.RateLimiter(max_attempts=5, window_seconds=10, clock=lambda: 0.0)
    auth._login_rate_limiter = limiter

    class DummyReq:
        def __init__(self):
            self.client = MagicMock(host="1.2.3.4")
            self.headers = {}
            self.app = app

    req = DummyReq()

    with pytest.MonkeyPatch().context() as m:
        async def local_no_user(*a, **kw):
            return None

        async def ldap_backend_error(*a, **kw):
            from app.auth import LDAPConfigError

            raise LDAPConfigError("backend down")

        m.setattr(auth, "authenticate_user", local_no_user)
        m.setattr(auth, "authenticate_ldap_user", ldap_backend_error)
        # Must not raise; must return the uniform 401 template.
        resp = await auth.login(req, email="user@test.com", password="bad")

    assert resp == "tpl"
    app.state.templates.TemplateResponse.assert_called()
    # The failed attempt is still counted against the rate limit (proves the
    # backend-error path took the uniform-failure branch, not a bare 500).
    assert len(limiter._events["1.2.3.4:user@test.com"]) == 1
