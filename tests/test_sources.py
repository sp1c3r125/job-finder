from jobfinder.sources import ashby, greenhouse, lever
from .conftest import FakeHttp


def test_greenhouse():
    url = greenhouse.URL.format(board="acme")
    http = FakeHttp({url: {"jobs": [{"id": 5, "title": "AI Engineer", "absolute_url": "https://gh/5",
        "location": {"name": "Remote - APAC"}, "first_published": "2026-11-20T10:00:00-04:00",
        "content": "&lt;p&gt;Build &lt;b&gt;agents&lt;/b&gt;&lt;/p&gt;"}]}})
    [j] = greenhouse.fetch(http, {"name": "Acme", "board": "acme"})
    assert (j.source, j.source_id, j.remote, j.description) == ("greenhouse", "5", True, "Build agents")
    assert j.posted.startswith("2026-11-20T14:00")
    assert http.calls[0][1] == {"content": "true"}


def test_lever_and_eu_host():
    url = lever.EU_URL.format(board="acme")
    http = FakeHttp({url: [{"id": "a", "text": "LLM Eng", "hostedUrl": "https://lv/a", "createdAt": 1786469891368,
        "categories": {"location": "Manila"}, "workplaceType": "remote", "descriptionPlain": "x"}]})
    [j] = lever.fetch(http, {"name": "Acme", "board": "acme", "region": "eu"})
    assert j.remote is True and j.location == "Manila" and j.posted.startswith("2026-")


def test_ashby_hybrid_is_not_remote_and_unlisted_skipped():
    url = ashby.URL.format(board="acme")
    base = {"title": "T", "jobUrl": "u", "publishedAt": "2026-11-20T00:00:00.000+00:00", "location": "Berlin",
            "descriptionPlain": "d", "isRemote": True}
    http = FakeHttp({url: {"jobs": [dict(base, id="1", workplaceType="Hybrid"),
                                    dict(base, id="2", workplaceType="Remote"),
                                    dict(base, id="3", isListed=False)]}})
    jobs = ashby.fetch(http, {"name": "Acme", "board": "acme"})
    assert [(j.source_id, j.remote) for j in jobs] == [("1", False), ("2", True)]
