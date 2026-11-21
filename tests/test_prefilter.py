from jobfinder.prefilter import prefilter
from .conftest import CFG, make_job, recent


def test_pass_and_rule():
    assert prefilter(make_job(), CFG) == (True, "worldwide")


def test_rejections():
    assert prefilter(make_job(posted=recent(10)), CFG)[1] == "older than max_age_days"
    assert prefilter(make_job(remote=False), CFG)[1] == "not remote"
    assert prefilter(make_job(location="Remote - US only"), CFG)[1] == "location not allowed"
    assert prefilter(make_job(title="Accountant", description="Books."), CFG)[1] == "no keyword match"
    assert prefilter(make_job(posted=""), CFG)[1] == "no posted date"


def test_ai_keyword_is_whole_word():
    assert not prefilter(make_job(title="Chair maker", description="Maintain chairs."), CFG)[0]
