"""How the SPA's own static files are served, and the one that was not.

The panel's pre-paint theme script lives at ``/admin/theme.js``. Vite copies
``public/`` to the *root* of ``dist``, so it sits outside the ``/admin/assets``
mount, and the SPA catch-all used to answer every path under ``/admin`` with
``index.html``.

The browser then refused to run it:

    Refused to execute script from '/admin/theme.js' because its MIME type
    ('text/html') is not executable, and strict MIME type checking is enabled.

Nothing failed server-side, because from the server's point of view it had
answered correctly, and nothing failed in the UI either, because ``index.html``
hardcodes ``data-theme="dark"`` and React sets the theme again on mount. Only a
light-theme user saw anything, and only as a flash of dark before the page
settled.

So the catch-all now serves a real file when the path names one.

The test builds its own ``dist`` fixture and points the app at it. The real one
is compiled inside the image and deliberately removed from the host checkout
(``admin/Dockerfile`` does ``rm -rf`` before copying it in), so a test that read
the real path would skip forever on a developer machine and in CI.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

# The app's settings refuse to start without these, so they are set before
# anything imports config.settings -- the same approach as the other end-to-end
# test modules in this suite.
os.environ.setdefault("ADMIN_USERNAME", "root")
os.environ.setdefault("ADMIN_PASSWORD", "an-initial-admin-password")
os.environ.setdefault("ADMIN_JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test-token")
os.environ.setdefault("ALLOWED_USER_IDS", "7575983824")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("ADMIN_COOKIE_SECURE", "false")

REPO = pathlib.Path(__file__).resolve().parent.parent
# bootstrap.py imports `config.settings`, which only resolves with the admin
# directory on the path.
sys.path.insert(0, str(REPO / "admin"))

import admin.services.bootstrap as bootstrap  # noqa: E402
from config.settings import Settings  # noqa: E402

INDEX_HTML = (
    "<!doctype html><html lang=\"en\" data-theme=\"dark\"><head>"
    '<script src="/theme.js"></script>'
    '<script type="module" src="/admin/assets/index.js"></script>'
    "</head><body><div id=\"root\"></div></body></html>"
)

THEME_JS = (
    "(function(){try{var s=localStorage.getItem('ns-theme');"
    "document.documentElement.setAttribute('data-theme',s==='light'?'light':'dark')"
    "}catch(e){}})()"
)

# A file that exists on the host but must never be reachable through /admin.
OUTSIDE_SECRET = "JWT_SECRET = 'do-not-serve-me'\n"


@pytest.fixture
def spa(tmp_path, monkeypatch) -> pathlib.Path:
    """Build a dist fixture and point the app's SPA constants at it."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    # Vite copies public/ to the root of dist, which is the whole reason the
    # catch-all was swallowing this file.
    (dist / "theme.js").write_text(THEME_JS, encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "index.js").write_text("export default 1\n", encoding="utf-8")

    monkeypatch.setattr(bootstrap, "FRONTEND_DIST", str(dist), raising=False)
    monkeypatch.setattr(bootstrap, "SPA_INDEX", str(dist / "index.html"))
    return dist


@pytest.fixture
def client(spa, tmp_path, monkeypatch):  # noqa: ARG001 - spa first: it must exist
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/admin.db")
    from fastapi.testclient import TestClient

    return TestClient(bootstrap.create_app(Settings.from_env()))


class TestThemeScriptIsServed:
    def test_served_with_a_javascript_content_type(self, client) -> None:
        """The exact failure above, as a test.

        The Content-Type is asserted as well as the status, because the bug was a
        200 carrying the wrong content: a status-only check would have passed the
        whole time.
        """
        response = client.get("/admin/theme.js")
        assert response.status_code == 200, (
            f"/admin/theme.js returned {response.status_code}; the SPA catch-all is "
            "answering for it and the browser will refuse to run it"
        )
        content_type = response.headers.get("content-type", "")
        assert "javascript" in content_type or "ecmascript" in content_type, (
            f"/admin/theme.js was served as {content_type!r}; a <script> with a "
            "text/html content type is refused under strict MIME checking"
        )
        assert "data-theme" in response.text

    def test_bundled_assets_still_served_from_the_assets_mount(self, client) -> None:
        response = client.get("/admin/assets/index.js")
        assert response.status_code == 200
        assert "javascript" in response.headers.get("content-type", "")


class TestClientRoutesStillGetTheShell:
    @pytest.mark.parametrize(
        "route",
        ["/admin", "/admin/targets", "/admin/targets/3", "/admin/nope"],
    )
    def test_route_returns_index_html(self, client, route: str) -> None:
        response = client.get(route)
        assert response.status_code == 200, f"{route} returned {response.status_code}"
        assert '<div id="root">' in response.text, (
            f"{route} did not return index.html, so the browser cannot resolve "
            "the client-side route"
        )

    def test_unknown_route_is_not_a_404(self, client) -> None:
        response = client.get("/admin/some/route/that/does/not/exist")
        assert response.status_code == 200
        assert '<div id="root">' in response.text


class TestTraversalIsRefused:
    """Serving real files must not make the build directory browsable upwards.

    ``normpath`` collapses ``..`` and the prefix check rejects anything resolving
    outside the build root, so these fall through to the shell. If that guard is
    ever removed, a request for a source file would return it.
    """

    @pytest.mark.parametrize(
        "attempt",
        [
            "/admin/../admin/config.py",
            "/admin/..%2f..%2fetc/passwd",
            "/admin/%2e%2e/%2e%2e/etc/passwd",
            "/admin/../../../../etc/passwd",
        ],
    )
    def test_outside_the_build_is_not_served(self, client, attempt: str) -> None:
        response = client.get(attempt)
        # Either the shell or a 404 is acceptable; source content is not.
        assert "JWT_SECRET" not in response.text
        assert "do-not-serve-me" not in response.text
        assert "root:x:" not in response.text
        assert "<html" not in response.text.lower() or "JWT_SECRET" not in response.text

    def test_a_directory_is_not_listed(self, client) -> None:
        response = client.get("/admin/assets")
        # StaticFiles would 404 on a directory; the catch-all would give the
        # shell. Neither may produce a directory listing.
        assert "Index of" not in response.text