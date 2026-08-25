"""CORS has to admit every header the browser actually sends.

The landing page is served from a different origin than this API, so a header
missing from allow_headers fails the preflight and the request never arrives.
That silently broke the landing page's "am I signed in?" check: the nav kept
offering a login link to people who were already logged in.
"""

from fastapi.testclient import TestClient

from app.main import CORS_HEADERS


ORIGIN = "http://localhost:5599"


def preflight(client, path, method, headers):
    return client.options(
        path,
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


def test_auth_header_survives_preflight(app_client):
    """X-Auth-Token is how the landing page asks /api/auth/me who you are."""
    resp = preflight(app_client, "/api/auth/me", "GET", "x-auth-token")
    assert resp.status_code == 200
    allowed = resp.headers["access-control-allow-headers"].lower()
    assert "x-auth-token" in allowed


def test_admin_header_survives_preflight(app_client):
    resp = preflight(app_client, "/api/admin/pending", "GET", "x-admin-token")
    assert resp.status_code == 200
    assert "x-admin-token" in resp.headers["access-control-allow-headers"].lower()


def test_server_creation_preflight_passes(app_client):
    resp = preflight(app_client, "/api/servers", "POST", "content-type")
    assert resp.status_code == 200


def test_every_declared_header_is_actually_allowed(app_client):
    resp = preflight(app_client, "/api/auth/me", "GET", ",".join(CORS_HEADERS))
    allowed = resp.headers["access-control-allow-headers"].lower()
    for header in CORS_HEADERS:
        assert header.lower() in allowed


def test_get_is_permitted_not_just_post(app_client):
    """Status polling and /api/auth/me are GETs; an earlier config allowed
    only POST."""
    resp = preflight(app_client, "/api/auth/me", "GET", "x-auth-token")
    assert "GET" in resp.headers.get("access-control-allow-methods", "")
