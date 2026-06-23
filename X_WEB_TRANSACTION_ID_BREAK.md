# X (Twitter) `x-client-transaction-id` broke — X migrated the logged-out root shell to a new `x-web` client

**Status:** ✅ FIXED (2026-06-23) in `src/tweety/http.py` `get_home_html()` — adds a legacy-bundle fallback. Verified live in the prod `qinglong` container (`connect()` + authenticated read both succeed).
**First observed:** 2026-06-24 (LA 2026-06-23). Worked fine the previous 7 days (6-17 … 6-23).
**Impact:** Every authenticated action fails at connect time. Downstream, the `ScheduledJobs` viral pipeline can no longer schedule/post on X (`AUTO_SCHEDULE failed: Couldn't get animation key indices`). Reproduces consistently — **not** transient.

---

## ✅ RESOLUTION (corrects §2 below)

The earlier root cause was **only half right**. X did migrate to a new `x-web` Vite build, **but only for the logged-out root shell** (`https://x.com/` and `https://x.com/?mx=2`). The legacy `responsive-web/client-web` bundle — which still ships `ondemand.s.<hash>a.js` — is **still served** on other routes.

Measured from the prod container (2026-06-23), fetched the way the library fetches (no auth header):

| URL | logged-out | logged-in (cookies) |
|---|---|---|
| `/?mx=2`, `/` (root shell) | **new x-web, 0 `ondemand`** (~66 KB) | legacy, 212 `ondemand` |
| `/i/flow/login`, `/home`, `/explore` | **legacy, 212 `ondemand`** (~280 KB) | legacy, 212 `ondemand` |

**The actual bug:** `get_home_html()` fetches `https://x.com/?mx=2`, and `_init_local_api()` calls `remove_cookies()` first — so the library always hit the **logged-out root shell**, which is exactly the one route X moved to `x-web`. Nothing else about the transaction-id algorithm changed: the `loading-x-anim` frames and `twitter-site-verification` key are still SSR'd, and the indices `(s[N],16)` are still in the same `ondemand.s` file — we just stopped being served the page that references it.

(Aside: the index-extraction JS is genuinely absent from the new `x-web` logged-out bundle — all 242 of its chunks were checked for the TID epoch `1682924400`, `obfiowerehiring`, `loading-x-anim` handling and the `(s[N],16)` pattern; none present. It is lazy-loaded only in the authenticated app. So porting an upstream fix was a dead end — upstream (`iSarabjitDhiman/XClientTransaction` #38) broke the same day and had no fix.)

**The fix** (`get_home_html()`): after fetching the page, if `TransactionGenerator.get_on_demand_url()` can't resolve an `ondemand.s` URL (i.e. we got the new shell), re-fetch a legacy-bundle route (`/i/flow/login`, then `/home`) and use the first that resolves. Self-contained, touches no cookie/guest-token logic, and is resilient to the rollout being geo/edge-bucketed (the prod IP currently flip-flops between buckets).

> If X eventually migrates the login/timeline routes to `x-web` too, this fallback stops finding `ondemand` and the indices must instead be recovered from the authenticated `x-web` bundle (fetch the home **with** session cookies, then locate the lazy chunk holding the `(s[N],16)` indices). Not needed today.

---

## 1. Symptom / error

Any `TwitterAsync` auth (`load_auth_token` / `load_cookies` / `sign_in`) raises during `connect()`:

```
Exception: Couldn't get animation key indices
```

Full stack (from prod container, python 3.11):

```
viral_scheduler.py  -> app = await init_tweety()
tweety/auth.py:132  load_auth_token -> load_cookies
tweety/auth.py:115  load_cookies -> connect
tweety/auth.py:22   connect -> self.user = await self.request.verify_cookies()
tweety/http.py:341  verify_cookies -> await self.__get_response__(**data)
tweety/http.py:192  __get_response__ -> await self._init_local_api()
tweety/http.py:183  _init_local_api -> TransactionGenerator(home_page_html, on_demand_file_text=on_demand_file_text)
tweety/transaction.py:108  __init__ -> self.get_indices(...)
tweety/transaction.py:148  get_indices -> raise Exception("Couldn't get animation key indices")
```

This is the `x-client-transaction-id` header generator (`transaction.py`). It dies in `get_indices()` because `key_byte_indices` is empty.

---

## 2. Root cause (verified empirically, not a guess)

**X migrated its entire web client to a new build, and the `ondemand.s` JS file that the transaction-id algorithm reads its indices from no longer exists / is no longer referenced on the home page.**

The algorithm needs three inputs from the X home page response:
1. `twitter-site-verification` meta tag → the base `key` (`get_key`). **Still present.**
2. `loading-x-anim` SVG frame elements → `get_frames` / `get_2d_array`. **Still present (4 frames).**
3. **The key-byte indices** (`DEFAULT_ROW_INDEX`, `DEFAULT_KEY_BYTES_INDICES`) — historically extracted from `ondemand.s.<hash>a.js` via `INDICES_REGEX` matching expressions like `(s[7],16)`. **This is what broke.**

### Evidence (live fetch dumped in the prod container on 2026-06-24)

I patched `get_indices()`'s failure branch to dump the actual fetched home page + ondemand text right before the raise, then triggered one real auth with a valid session. Result:

```
home_len = 66276          # home page fetched fine, real size
on_demand_url = None      # <-- get_on_demand_url() could NOT resolve the URL
od_len = 0                # so on_demand_file_text is empty -> 0 indices -> raise
```

Inspecting the dumped `home.html` (the real X shell page — `<title>X. It's what's happening / X</title>`):

| token | count in home page |
|---|---|
| `twitter-site-verification` | 1 ✅ |
| `loading-x-anim` | 4 ✅ |
| `abs.twimg.com` | 245 |
| **`ondemand`** | **0** ❌ |
| **`client-web`** | **0** ❌ |
| `(\w[\d],16)` index pattern | 0 |

The JS chunk URLs in the home page are now of the form:

```
https://abs.twimg.com/x-web/x-web/entry-client-logged-out-CjJYjPAv.js
https://abs.twimg.com/x-web/x-web/assets/web-CbspW8J1.js
https://abs.twimg.com/x-web/x-web/assets/utils-Lt4298Ph.js
... (Vite build: assets/<name>-<shorthash>.js)
```

i.e. X moved from the old **`abs.twimg.com/responsive-web/client-web/...`** bundle (which served `ondemand.s.<hash>a.js`) to a new **`abs.twimg.com/x-web/x-web/...`** Vite build. The old `ondemand.s` file — and the home-page reference to it that `get_on_demand_url()` keys off — is gone.

I fetched the new entry chunk (`entry-client-logged-out-CjJYjPAv.js`, 35,979 bytes) and grepped it: it contains **none** of `obfiowerehiring`, `loading-x-anim`, `ondemand`, the `(\w[\d],16)` index pattern, or `animationKey`. So the transaction-id logic is **not** in the entry chunk — it has been split into some other chunk of the new `x-web` build (not yet located).

---

## 3. Where the broken code is

`src/tweety/transaction.py`:

- **`ON_DEMAND_FILE_REGEX`** (line ~17): `'ondemand.s':'<hash>'` — no longer present in the home page.
- **`ON_DEMAND_CHUNK_ID_REGEX`** (line ~19): `<chunkid>:"ondemand.s"` — also absent.
- **`get_on_demand_url()`** (line ~113): returns `None` now → no file fetched.
- **`INDICES_REGEX`** (line ~21): `(\w[\d{1,2}],16)` — would also need re-checking once the new source file is found, since the new bundle is minified differently.
- **`get_indices()`** (line ~132): raises at line ~148 when indices come back empty.

`src/tweety/http.py` `_init_local_api()` is the caller; it resolves `get_on_demand_url()` from the home page and `await self._session.get(on_demand_url)`. Since the URL is `None`, nothing is fetched.

---

## 4. Fix directions (pick one)

### Option A — Port an upstream fix (highest ROI, try first)
This X `x-web` migration is global; the upstream projects almost certainly hit it too. Compare against the latest:
- `mahrtayyab/tweety` (this is a fork of it) — `tweety/transaction.py` + `tweety/http.py`.
- `iSarabjitDhiman/TweeterPy` — the original credit source for this TID code (`tweeterpy/tid`).

If upstream now resolves the indices from the new `x-web` bundle (or switched to a different scheme), port that change here and reconcile with this fork's customizations. The prod container **can reach the internet**, so you can pip-compare / fetch upstream there.

### Option B — Re-RE the new `x-web` bundle (works but brittle)
Locate the chunk in `abs.twimg.com/x-web/x-web/assets/*.js` that now contains the index-extraction code (look for the `(\w[\d],16)` pattern, the `obfiowerehiring` keyword, or `loading-x-anim` handling), then update `get_on_demand_url()` / `get_indices()` to fetch indices from the new location. Expect X to keep rotating hashes and occasionally re-break this.

### Option C — Don't depend on `ondemand.s` at all
If the new client computes the TID without a per-deploy indices file (e.g. fixed indices, or indices embedded in the inline home-page boot script), adapt `get_indices()` to read them from the home page directly, with a sane hardcoded fallback when neither source is present.

> Whatever the fix, the goal is: `get_indices()` returns valid `(DEFAULT_ROW_INDEX, DEFAULT_KEY_BYTES_INDICES)` so `generate_transaction_id()` produces a header X accepts. `get_key` (verification meta) and `get_frames` (`loading-x-anim`) still work, so only the indices source needs repair (verify the frames math too once indices are back).

---

## 5. How to reproduce / debug (prod container)

The library is exercised live in the `qinglong` Docker container on the prod server (`192.168.1.3`). It can reach X and the internet (local dev machines here cannot — OpenAI/GitHub are network-blocked).

- Deployed copy of this lib: `/ql/data/dep_cache/python3/lib/python3.11/site-packages/tweety/`
- A valid X session lives in QingLong's env (`TWEETY_SESSION`, `X_AUTH_TOKEN`), stored in the `Envs` table of `/ql/data/db/database.sqlite` (cols `name,value,status`; status 0 = enabled). `task`/`sqlite3` CLIs are NOT on the default `docker exec` PATH — load env yourself from that sqlite with python3's built-in `sqlite3`.
- Container TZ is `Asia/Shanghai`.

**Debug recipe that produced the evidence above:** patch `get_indices`'s `if not key_byte_indices:` branch to dump `str(response)` (home page) and `on_demand_file_text` to `/tmp/home.html` / `/tmp/ondemand.js` and a summary to `/tmp/tid_debug.txt` *before* the `raise`; then trigger a single auth (e.g. build a `TwitterAsync`, `load_auth_token(TWEETY_SESSION)`), and inspect the dumps inside the container (`docker cp` or `docker exec cat`). Remember host `/tmp` ≠ container `/tmp`.

**Deploying a fix:** there's no git auto-deploy for this container — after fixing `src/tweety/`, reinstall/overwrite the package under `/ql/data/dep_cache/python3/lib/python3.11/site-packages/tweety/` and re-run an auth to verify `verify_cookies()` succeeds (no more "Couldn't get animation key indices"). Then re-run the ScheduledJobs `viral_scheduler` with `--platforms x` against an existing `viral_selected_*.html` to confirm scheduling works end-to-end.

---

## 6. Cross-repo notes (ScheduledJobs side — not this repo's job, just context)

- The ScheduledJobs viral pipeline calls this lib via `X/viral_scheduler.py::init_tweety()`. While X is down, that repo is posting to **Bluesky only** (Bluesky doesn't use tweety).
- There's also a coupling bug in `ScheduledJobs/X/viral_scheduler.py::run()` — it inits tweety (X) *before* enqueuing Bluesky, so a tweety failure also skips Bluesky. That's a separate fix tracked on the ScheduledJobs side; it does **not** affect the tweety fix here.
