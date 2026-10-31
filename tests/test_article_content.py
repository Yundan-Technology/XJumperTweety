"""X Article support: markdown -> content_state and the GraphQL request payloads."""
from tweety.article_content import markdown_to_content_state
from tweety.builder import UrlBuilder


def _types(cs):
    return [b["type"] for b in cs["blocks"]]


def test_blocks_styles_and_links():
    cs = markdown_to_content_state(
        "# Title\n\nHello **big** [link](https://x.com) and *it*.\n\n- a\n- b\n\n1. one\n\n> quoted\n> more\n\n---\n\nend"
    )
    assert _types(cs) == ["header-two", "unstyled", "unordered-list-item", "unordered-list-item",
                          "ordered-list-item", "blockquote", "atomic", "unstyled"]
    para = cs["blocks"][1]
    assert para["text"] == "Hello big link and it."
    assert {"style": "Bold", "offset": 6, "length": 3} in para["inline_style_ranges"]
    assert {"style": "Italic", "offset": 19, "length": 2} in para["inline_style_ranges"]
    assert para["entity_ranges"] == [{"key": 0, "offset": 10, "length": 4}]
    assert cs["entity_map"][0] == {"key": "0", "value": {"type": "LINK", "mutability": "Mutable",
                                                         "data": {"url": "https://x.com"}}}
    assert cs["entity_map"][1]["value"]["type"] == "DIVIDER"
    assert cs["blocks"][5]["text"] == "quoted more"


def test_offsets_count_utf16_units():
    cs = markdown_to_content_state("end \U0001F600 **x**")
    assert cs["blocks"][0]["inline_style_ranges"] == [{"style": "Bold", "offset": 7, "length": 1}]


def test_wrapped_paragraph_is_joined_and_keys_are_unique():
    cs = markdown_to_content_state("line one\nline two\n\nnext")
    assert [b["text"] for b in cs["blocks"]] == ["line one line two", "next"]
    assert len({b["key"] for b in cs["blocks"]}) == 2


def test_tables_become_list_rows():
    cs = markdown_to_content_state("| Wallet | Return |\n|---|---|\n| 0xab..cd | 12% |\n| 0xef..01 | 9% |")
    assert [b["text"] for b in cs["blocks"]] == ["Wallet: 0xab..cd, Return: 12%", "Wallet: 0xef..01, Return: 9%"]
    assert set(_types(cs)) == {"unordered-list-item"}


def test_code_fence_and_images():
    cs = markdown_to_content_state("```\ncode\n```\n\n![a](https://img/1.png)\n\n![b](https://img/2.png)",
                                   media={"https://img/1.png": {"media_id": 42}})
    assert _types(cs) == ["atomic", "atomic"]
    assert cs["entity_map"][0]["value"] == {"type": "MARKDOWN", "mutability": "Mutable", "data": {"markdown": "```\ncode\n```"}}
    item = cs["entity_map"][1]["value"]["data"]["mediaItems"][0]
    assert item["mediaId"] == "42" and item["mediaCategory"] == "DraftTweetImage"


def test_link_inside_bold():
    cs = markdown_to_content_state("**see [board](https://t.co/x)**")
    blk = cs["blocks"][0]
    assert blk["text"] == "see board"
    assert blk["entity_ranges"] == [{"key": 0, "offset": 4, "length": 5}]


def test_builder_payloads():
    b = UrlBuilder()
    req = b.article_draft_create("T", {"blocks": [], "entity_map": []})
    assert req["method"] == "POST" and req["url"].endswith("/ArticleEntityDraftCreate")
    assert req["json"]["queryId"] == "fCkdrI6zrnw_UtVa3WDHhw"
    assert req["json"]["variables"] == {"content_state": {"blocks": [], "entity_map": []}, "title": "T"}
    assert req["json"]["features"]["responsive_web_graphql_timeline_navigation_enabled"] is True

    assert b.article_update_content(7, {"x": 1})["json"]["variables"] == {"content_state": {"x": 1}, "article_entity": "7"}
    assert b.article_update_cover(7, 99)["json"]["variables"] == {
        "articleEntityId": "7", "coverMedia": {"media_id": "99", "media_category": "DraftTweetImage"}}
    assert b.article_update_cover(7, None)["json"]["variables"] == {"articleEntityId": "7"}
    assert b.article_publish(7, "hi")["json"]["variables"] == {"articleEntityId": "7", "tweetText": "hi"}
    assert b.article_delete(7)["json"]["features"] == {}
    got = b.article_by_id(7)
    assert got["method"] == "GET" and '"articleEntityId": "7"' in got["params"]["variables"]


def _user(responses):
    from unittest.mock import AsyncMock, MagicMock
    from tweety.user import UserMethods

    u = UserMethods.__new__(UserMethods)
    u.http = MagicMock()
    u.http.article_request = AsyncMock(side_effect=responses)
    u._upload_media = AsyncMock(return_value=[MagicMock(media_id="555")])
    u.is_user_authorized = True
    u.user = MagicMock()
    return u


def test_create_article_flow_reports_draft_first_and_parses_tweet_id():
    import asyncio

    draft = {"data": {"articleentity_create_draft": {"article_entity_results": {"result": {"rest_id": "100"}}}}}
    cover = {"data": {"articleentity_update_cover_media": {}}}
    published = {"data": {"articleentity_publish": {"article_entity_results": {"result": {
        "rest_id": "100", "metadata": {"tweet_results": {"result": {"rest_id": "900"}}}}}}}}
    u = _user([draft, cover, published])
    seen = []
    res = asyncio.run(u.create_article("T", {"blocks": []}, cover="/tmp/c.png", tweet_text="t", on_draft=seen.append))
    assert seen == ["100"]
    assert res["article_id"] == "100" and res["tweet_id"] == "900"
    names = [c.args[0] for c in u.http.article_request.call_args_list]
    assert names == ["article_draft_create", "article_update_cover", "article_publish"]


def test_article_errors_raise():
    import asyncio
    import pytest

    u = _user([{"errors": [{"code": "X", "message": "nope"}]}])
    with pytest.raises(ValueError, match="nope"):
        asyncio.run(u.create_article_draft("T", {"blocks": []}))
