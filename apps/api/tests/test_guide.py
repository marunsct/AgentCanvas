"""The user guide in the app: listed, read by page name, and nothing outside its folder."""


async def test_the_guide_lists_its_pages_and_serves_one(api_client):
    pages = (await api_client.get("/v1/guide")).json()
    assert pages[0]["page"] == "index" and {"models", "deploy"} <= {p["page"] for p in pages}
    models = (await api_client.get("/v1/guide/models")).json()
    assert models["title"] and models["markdown"].startswith("# ")


async def test_only_guide_pages_can_be_read(api_client):
    for bad in ("nope", "..%2Fsettings", "Models", "models.md"):
        assert (await api_client.get(f"/v1/guide/{bad}")).status_code == 404, bad
