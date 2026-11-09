"""G-6: a knowledge base's web pages and sitemaps are fetched again on a schedule: changed pages replace their
document, new sitemap pages are added, pages that left the sitemap are removed, and every fetch keeps the
private-address refusal. Pages are served by a mock transport."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from agentcanvas.services import knowledge as kn

P = "/v1/projects/prj_default"


def page(title: str, body: str) -> httpx.Response:
    html = f"<html><title>{title}</title><body><h1>{title}</h1><p>{body}</p></body></html>"
    return httpx.Response(200, text=html, headers={"content-type": "text/html"})


def sitemap(*paths: str, host: str = "shop.example") -> httpx.Response:
    locs = "".join(f"<url><loc>https://{host}{p}</loc></url>" for p in paths)
    xml = f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'
    return httpx.Response(200, text=xml, headers={"content-type": "application/xml"})


class Site:
    """A small web site whose pages the test edits between crawls."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[str, str]] = {
            "/shipping": ("Shipping", "Orders ship within three days."),
            "/returns": ("Returns", "Returns are accepted for thirty days."),
        }
        self.listed = ["/shipping", "/returns"]
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if path == "/sitemap.xml":
            return sitemap(*self.listed)
        if path == "/old-shipping":
            return httpx.Response(301, headers={"location": "/shipping"})
        if path in self.pages:
            return page(*self.pages[path])
        return httpx.Response(404)


async def kb_with(api, app, site: Site, name: str = "web") -> str:
    app.state.knowledge_transport = httpx.MockTransport(site)
    r = await api.post(f"{P}/knowledge", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def titles(api, kb_id: str) -> list[str]:
    return sorted(d["name"] for d in (await api.get(f"/v1/knowledge/{kb_id}/documents")).json())


async def test_a_changed_page_replaces_its_document_and_is_searched_by_its_new_text(app, api_client):
    site = Site()
    kb_id = await kb_with(api_client, app, site)
    k = f"/v1/knowledge/{kb_id}"
    assert (
        await api_client.post(f"{k}/documents/url", json={"url": "https://shop.example/shipping"})
    ).status_code == 201
    before = (await api_client.get(k)).json()["version"]

    r = await api_client.post(f"{k}/recrawl")
    assert r.status_code == 200, r.text
    assert (r.json()["unchanged"], r.json()["updated"], r.json()["failed"]) == (1, 0, [])
    assert (await api_client.get(k)).json()["version"] == before  # nothing changed: the search caches stay valid

    site.pages["/shipping"] = ("Shipping", "Orders now ship the same day from our new warehouse.")
    r = await api_client.post(f"{k}/recrawl")
    assert (r.json()["updated"], r.json()["unchanged"]) == (1, 0), r.text
    assert (await api_client.get(k)).json()["documents"] == 1  # replaced, not added next to the old one
    found = (await api_client.post(f"{k}/search", json={"query": "same day warehouse", "mode": "keyword"})).json()
    assert "same day" in found["results"][0]["text"]
    old = (await api_client.post(f"{k}/search", json={"query": "three days", "mode": "keyword"})).json()["results"]
    assert old == []
    assert (await api_client.get(k)).json()["recrawl"]["last"]["updated"] == 1


async def test_a_page_that_cannot_be_fetched_keeps_its_document(app, api_client):
    site = Site()
    kb_id = await kb_with(api_client, app, site)
    k = f"/v1/knowledge/{kb_id}"
    await api_client.post(f"{k}/documents/url", json={"url": "https://shop.example/returns"})
    del site.pages["/returns"]
    r = (await api_client.post(f"{k}/recrawl")).json()
    assert r["updated"] == r["removed"] == 0 and r["failed"][0]["url"] == "https://shop.example/returns"
    assert "404" in r["failed"][0]["error"]
    assert await titles(api_client, kb_id) == ["Returns"]


async def test_a_page_update_that_fails_does_not_break_the_rest_of_the_crawl(app, api_client):
    """I-4: the rollback after a failed page update expired every loaded document, and reading the next one's hash
    raised MissingGreenlet: the re-crawl answered 500."""
    site = Site()
    kb_id = await kb_with(api_client, app, site)
    k = f"/v1/knowledge/{kb_id}"
    for p in ("shipping", "returns"):
        r = await api_client.post(f"{k}/documents/url", json={"url": f"https://shop.example/{p}"})
        assert r.status_code == 201
    site.pages["/shipping"] = ("Returns", "Returns are accepted for thirty days.")  # the text returns has: refused
    site.pages["/returns"] = ("Returns", "Returns are accepted for sixty days now.")
    r = await api_client.post(f"{k}/recrawl")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["updated"] == 1 and [f["url"] for f in got["failed"]] == ["https://shop.example/shipping"]
    docs = {d["url"]: d for d in (await api_client.get(f"{k}/documents")).json()}
    assert docs["https://shop.example/shipping"]["name"] == "Shipping"  # kept as it was


async def test_a_sitemap_never_removes_a_page_added_by_hand(app, api_client):
    """I-17: a page added by hand that a sitemap also listed was adopted by it, and removed once the sitemap stopped
    listing it."""
    site = Site()
    kb_id = await kb_with(api_client, app, site)
    k = f"/v1/knowledge/{kb_id}"
    assert (
        await api_client.post(f"{k}/documents/url", json={"url": "https://shop.example/returns"})
    ).status_code == 201
    r = await api_client.post(f"{k}/documents/sitemap", json={"url": "https://shop.example/sitemap.xml"})
    assert r.status_code == 201 and r.json()["added"] == 1, r.text
    site.listed = ["/shipping"]
    r = (await api_client.post(f"{k}/recrawl")).json()
    assert r["removed"] == 0, r
    assert await titles(api_client, kb_id) == ["Returns", "Shipping"]
    site.listed = []  # the page the sitemap added does follow it
    assert (await api_client.post(f"{k}/recrawl")).json()["removed"] == 1
    assert await titles(api_client, kb_id) == ["Returns"]


async def test_a_sitemap_adds_its_pages_and_follows_it(app, api_client):
    site = Site()
    kb_id = await kb_with(api_client, app, site)
    k = f"/v1/knowledge/{kb_id}"
    r = await api_client.post(f"{k}/documents/sitemap", json={"url": "https://shop.example/sitemap.xml"})
    assert r.status_code == 201, r.text
    assert r.json()["added"] == 2 and r.json()["failed"] == []
    assert await titles(api_client, kb_id) == ["Returns", "Shipping"]
    assert (await api_client.get(k)).json()["recrawl"]["sitemaps"] == ["https://shop.example/sitemap.xml"]

    site.pages["/warranty"] = ("Warranty", "Every product has a two year warranty.")
    site.listed = ["/shipping", "/warranty"]  # returns left the sitemap, warranty is new
    r = (await api_client.post(f"{k}/recrawl")).json()
    assert (r["added"], r["removed"], r["unchanged"]) == (1, 1, 1), r
    assert await titles(api_client, kb_id) == ["Shipping", "Warranty"]
    found = (await api_client.post(f"{k}/search", json={"query": "warranty", "mode": "keyword"})).json()["results"]
    assert found and found[0]["source"] == "https://shop.example/warranty"

    # a page the person deleted by hand is not added back, and one a sitemap lists twice is added once
    docs = (await api_client.get(f"{k}/documents")).json()
    gone = next(d for d in docs if d["name"] == "Warranty")
    assert (await api_client.delete(f"{k}/documents/{gone['id']}")).status_code == 204
    r = (await api_client.post(f"{k}/recrawl")).json()
    assert (r["added"], r["removed"]) == (0, 0)
    assert await titles(api_client, kb_id) == ["Shipping"]


async def test_a_sitemap_index_and_other_hosts(app, api_client):
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/index.xml":
            xml = (
                '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                "<sitemap><loc>https://shop.example/part1.xml</loc></sitemap></sitemapindex>"
            )
            return httpx.Response(200, text=xml)
        if request.url.path == "/part1.xml":
            return sitemap("/a", "/a")  # a repeated address
        if request.url.path == "/evil.xml":
            locs = "<url><loc>https://other.example/x</loc></url><url><loc>ftp://shop.example/y</loc></url>"
            return httpx.Response(
                200, text=f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'
            )
        if request.url.path == "/bomb.xml":
            return httpx.Response(
                200, text='<!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>'
            )
        return httpx.Response(404)

    t = httpx.MockTransport(serve)
    assert await kn.fetch_sitemap("https://shop.example/index.xml", t) == ["https://shop.example/a"]
    assert await kn.fetch_sitemap("https://shop.example/evil.xml", t) == []  # only the sitemap's own host, http(s)
    app.state.knowledge_transport = t
    kb_id = (await api_client.post(f"{P}/knowledge", json={"name": "idx"})).json()["id"]
    k = f"/v1/knowledge/{kb_id}"
    for path in ("/bomb.xml", "/evil.xml"):  # entity declarations are refused; an empty sitemap adds nothing
        r = await api_client.post(f"{k}/documents/sitemap", json={"url": f"https://shop.example{path}"})
        assert r.status_code == 422, (path, r.text)


async def test_sitemaps_keep_the_private_address_refusal(app, api_client):
    kb_id = (await api_client.post(f"{P}/knowledge", json={"name": "sm"})).json()["id"]
    k = f"/v1/knowledge/{kb_id}"
    for url in ("http://127.0.0.1/sitemap.xml", "http://169.254.169.254/sitemap.xml", "file:///etc/passwd"):
        r = await api_client.post(f"{k}/documents/sitemap", json={"url": url})  # no mock transport: the real resolver
        assert r.status_code == 422, (url, r.text)
    assert (await api_client.get(k)).json()["recrawl"]["sitemaps"] == []


async def test_the_crawl_job_recrawls_a_base_once_per_interval(app, api_client):
    site = Site()
    kb_id = await kb_with(api_client, app, site)
    k = f"/v1/knowledge/{kb_id}"
    await api_client.post(f"{k}/documents/url", json={"url": "https://shop.example/shipping"})
    assert (await api_client.put(f"{k}/recrawl", json={"every_hours": 0})).status_code == 422
    r = await api_client.put(f"{k}/recrawl", json={"every_hours": 24})
    assert r.status_code == 200 and r.json()["recrawl"]["every_hours"] == 24

    job = kn.CrawlJob(app.state.db, kn.builtin_only, lambda _: {}, transport=lambda: app.state.knowledge_transport)
    other = kn.CrawlJob(app.state.db, kn.builtin_only, lambda _: {}, transport=lambda: app.state.knowledge_transport)
    now = datetime.now(UTC)
    assert await job.tick(now + timedelta(hours=1)) == []  # set up just now: due in a day
    site.pages["/shipping"] = ("Shipping", "Orders ship within one day.")
    day = now + timedelta(hours=24, minutes=1)
    # two replicas ticking together: one claims the base and crawls it
    done = [*await job.tick(day), *await other.tick(day)]
    assert done == [kb_id]
    assert (await api_client.get(k)).json()["recrawl"]["last"]["updated"] == 1
    assert await job.tick(day + timedelta(hours=1)) == []  # not again before the next interval
    assert await job.tick(day + timedelta(hours=24)) == [kb_id]

    assert (await api_client.put(f"{k}/recrawl", json={"every_hours": None})).json()["recrawl"]["every_hours"] is None
    assert await job.tick(day + timedelta(days=9)) == []  # off


async def test_viewers_cannot_start_a_recrawl(app, api_client):
    from agentcanvas.api.deps import CurrentUser, current_user

    site = Site()
    kb_id = await kb_with(api_client, app, site)
    k = f"/v1/knowledge/{kb_id}"
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="usr_dev", org_id="org_default", role="viewer", memberships={"org_default": "viewer"}
    )
    try:
        assert (await api_client.post(f"{k}/recrawl")).status_code == 403
        assert (await api_client.put(f"{k}/recrawl", json={"every_hours": 24})).status_code == 403
        assert (
            await api_client.post(f"{k}/documents/sitemap", json={"url": "https://shop.example/s.xml"})
        ).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)
