"""Regression tests for the x-web home-shell migration fallback in get_home_html.

Background: X migrated the logged-out root shell ("/" and "/?mx=2") to a new "x-web"
Vite build that no longer references the ondemand.s JS file the transaction-id generator
reads its key-byte indices from. get_home_html() must detect that shell (no resolvable
ondemand URL) and fall back to a legacy-bundle route so connect()/verify_cookies() keep
working. See X_WEB_TRANSACTION_ID_BREAK.md.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest
from tweety.http import Request


# Minimal HTML fixtures. get_on_demand_url() resolves a URL only for the legacy bundle.
XWEB_SHELL_HTML = (
    b"<html><head><title>X. It's what's happening / X</title>"
    b'<script src="https://abs.twimg.com/x-web/x-web/entry-client-logged-out-CjJYjPAv.js">'
    b"</script></head><body>no ondemand here</body></html>"
)
LEGACY_BUNDLE_HTML = (
    b"<html><head><title>X</title>"
    b'<script>window.__meta={"ondemand.s":"deadbeef"}</script>'
    b"</head><body>legacy responsive-web bundle</body></html>"
)


def _make_response(content, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    return response


def _make_request():
    client = MagicMock()
    request = Request(client, max_retries=3)
    # Avoid touching real client/session state for header building.
    request._get_request_headers = MagicMock(return_value={})
    request._session = MagicMock()
    return request


@pytest.mark.asyncio
async def test_falls_back_to_legacy_route_when_primary_is_xweb_shell():
    """Primary /?mx=2 returns the new x-web shell -> fall back to a legacy-bundle route."""
    request = _make_request()
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append(url)
        # First (primary) call returns the new shell; fallback route returns the legacy bundle.
        if url == "https://x.com/?mx=2":
            return _make_response(XWEB_SHELL_HTML)
        return _make_response(LEGACY_BUNDLE_HTML)

    request._session.request = AsyncMock(side_effect=fake_request)

    home_page = await request.get_home_html()

    # The returned page must be the legacy one (resolvable ondemand URL).
    assert Request.HOME_PAGE_ONDEMAND_FALLBACK_URLS, "fallback list must not be empty"
    from tweety.transaction import TransactionGenerator
    assert TransactionGenerator.get_on_demand_url(str(home_page)) is not None
    # Primary fetched first, then at least one fallback route.
    assert calls[0] == "https://x.com/?mx=2"
    assert any(c in Request.HOME_PAGE_ONDEMAND_FALLBACK_URLS for c in calls[1:])


@pytest.mark.asyncio
async def test_no_fallback_when_primary_already_has_ondemand():
    """If the primary fetch already exposes ondemand, do not fetch any fallback route."""
    request = _make_request()
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append(url)
        return _make_response(LEGACY_BUNDLE_HTML)

    request._session.request = AsyncMock(side_effect=fake_request)

    home_page = await request.get_home_html()

    from tweety.transaction import TransactionGenerator
    assert TransactionGenerator.get_on_demand_url(str(home_page)) is not None
    # Only the primary URL was fetched; no fallback route was hit.
    assert calls == ["https://x.com/?mx=2"]
