import time

from jobfinder.sources import himalayas
from .conftest import CFG, FakeHttp


def test_himalayas_keeps_expiry_date():
    exp = int(time.time()) + 86400

    class H(FakeHttp):
        def get_json(self, url, params=None):
            return {"jobs": [{"guid": "g", "title": "AI Eng", "companyName": "Acme", "applicationLink": "g",
                              "locationRestrictions": [], "pubDate": int(time.time()), "expiryDate": exp,
                              "description": "x"}] if params["page"] == 1 else []}

    [j] = himalayas.fetch(H({}), dict(CFG, keywords=["AI"], himalayas={}))
    assert j.expires.startswith("20") and j.expires != ""
