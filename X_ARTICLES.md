# X Articles (long-form posts)

Added 2026-10-31. Lets a logged-in **Premium** account draft, publish, unpublish and delete X Articles, the same way the x.com Article editor does.

```python
from tweety import TwitterAsync
from tweety.article_content import markdown_to_content_state

app = TwitterAsync("session")
await app.load_auth_token(AUTH_TOKEN)
content = markdown_to_content_state(markdown_body)
res = await app.create_article("Title", content, cover="cover.png", tweet_text="optional post text",
                               on_draft=lambda article_id: save(article_id))
# res = {"article_id": "...", "tweet_id": "..." or None, "result": {...}}
```

Lower level: `create_article_draft`, `update_article_title`, `update_article_content`, `set_article_cover`, `publish_article`, `unpublish_article`, `delete_article`, `get_article`.

## What X expects (found by probing, keep in mind when it breaks)

- GraphQL mutations `ArticleEntityDraftCreate`, `ArticleEntityUpdateContent`, `ArticleEntityUpdateTitle`, `ArticleEntityUpdateCoverMedia`, `ArticleEntityPublish`, `ArticleEntityUnpublish`, `ArticleEntityDelete`, query `ArticleEntityResultByRestId`. The query ids live in `builder.py` and come from the lazy chunk `bundle.TwitterArticles` (load `https://x.com/compose/articles` logged in, resolve the chunk name and hash from `main.*.js`). They rotate when X ships a new client; a `404` or `GRAPHQL_VALIDATION_FAILED` on every call usually means new ids.
- **Input uses snake_case, output camelCase.** Writes need `content_state = {"blocks": [...], "entity_map": [...]}` with `inline_style_ranges` / `entity_ranges` on each block. Sending the camelCase names X returns fails with `GRAPHQL_VALIDATION_FAILED` on `entityMap`.
- `entity_map` is a list of `{"key": "0", "value": {"type", "mutability", "data"}}`. Types seen: `LINK` (`{"url"}`), `MEDIA` (`{"mediaItems": [{"mediaCategory": "DraftTweetImage", "mediaId", "localMediaId"}]}`), `DIVIDER`, `MARKDOWN` (`{"markdown": "```...```"}`, code blocks).
- Block types seen: `unstyled`, `header-two`, `blockquote`, `unordered-list-item`, `ordered-list-item`, `atomic` (one space of text carrying a MEDIA / DIVIDER / MARKDOWN entity). Inline styles: `Bold`, `Italic`. Offsets count UTF-16 code units.
- Cover: upload the image as a normal tweet image, then `ArticleEntityUpdateCoverMedia` with `media_category: "DraftTweetImage"`.
- Draft create, content, cover and delete were verified live on 2026-10-31. Publish was built from the client code; if X answers without a tweet id, `tweet_id` is `None` and the Article is still reachable at `https://x.com/i/article/<article_id>`.
