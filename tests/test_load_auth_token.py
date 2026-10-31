"""load_auth_token gets ct0 from x.com first; business.x.com (Cloudflare challenge since
2026-10) is only a fallback."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from tweety.auth import AuthMethods


def _res(ct0=None):
    r = MagicMock()
    r.cookies = {"ct0": ct0} if ct0 else {}
    return r


@pytest.mark.asyncio
async def test_ct0_from_x_com_without_touching_business():
    bot = MagicMock(AUTH_TOKEN_CT0_URLS=AuthMethods.AUTH_TOKEN_CT0_URLS)
    urls = []

    async def get(url, **kw):
        urls.append(url)
        return _res("tok" if url == "https://x.com/" else None)

    bot.request.session.get = AsyncMock(side_effect=get)
    bot.load_cookies = AsyncMock(return_value="user")
    assert await AuthMethods.load_auth_token(bot, "a") == "user"
    assert urls == ["https://x.com/"]
    bot.load_cookies.assert_awaited_once_with({"auth_token": "a", "ct0": "tok"})


@pytest.mark.asyncio
async def test_falls_back_to_business_when_x_com_gives_no_ct0():
    bot = MagicMock(AUTH_TOKEN_CT0_URLS=AuthMethods.AUTH_TOKEN_CT0_URLS)
    urls = []

    async def get(url, **kw):
        urls.append(url)
        return _res("tok" if "business" in url else None)

    bot.request.session.get = AsyncMock(side_effect=get)
    bot.load_cookies = AsyncMock(return_value="user")
    await AuthMethods.load_auth_token(bot, "a")
    assert urls[-1] == "https://business.x.com/en" and urls[0] == "https://x.com/"
