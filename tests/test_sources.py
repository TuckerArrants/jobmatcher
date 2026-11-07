from jobmatcher.models import canonical_url, posting_id
from jobmatcher.sources import ashby, greenhouse, lever
from jobmatcher.sources.http import Http, NotFound, SourceError

from conftest import fixture


def test_canonical_url_drops_tracking_keeps_job_params():
    a = canonical_url("HTTPS://Boards.Greenhouse.io/x/jobs/1/?utm_source=li&gh_src=abc#apply")
    b = canonical_url("https://boards.greenhouse.io/x/jobs/1")
    assert a == b
    assert canonical_url("https://x.com/careers?gh_jid=5") != canonical_url("https://x.com/careers?gh_jid=6")
    assert posting_id("https://a.com/j/1") == posting_id("https://a.com/j/1/")


def test_greenhouse_parse():
    jobs = greenhouse.parse(fixture("greenhouse.json"), "Example Bio")
    assert len(jobs) == 2  # the job without a URL is skipped
    ds = jobs[0]
    assert ds.title == "Data Scientist, Clinical Imaging"
    assert ds.locations == ["Boston, MA", "Remote - US"]
    assert ds.workplace == "remote"
    assert (ds.salary_min, ds.salary_max) == (130000, 160000)  # read before boilerplate is cut
    assert "MRI" in ds.text
    assert "snacks" not in ds.text and "equal opportunity" not in ds.text.lower()
    assert ds.posted_at.year == 2026
    assert jobs[1].workplace == "onsite"


def test_lever_parse():
    jobs = lever.parse(fixture("lever.json"), "Example Fund")
    qr, da = jobs
    assert qr.locations == ["New York, NY", "Chicago, IL"]
    assert qr.workplace == "onsite"
    assert (qr.salary_min, qr.salary_max) == (175000, 250000)
    assert "5+ years" in qr.text and "equal opportunity" not in qr.text.lower()
    assert da.workplace == "remote"
    assert (da.salary_min, da.salary_max) == (40 * 2080, 50 * 2080)


def test_ashby_parse():
    jobs = ashby.parse(fixture("ashby.json"), "Example Chem")
    assert len(jobs) == 1  # unlisted job skipped
    (ml,) = jobs
    assert ml.locations == ["San Francisco, CA", "Remote (US)"]
    assert ml.workplace == "hybrid"  # explicit workplaceType wins over isRemote
    assert (ml.salary_min, ml.salary_max) == (150000, 200000)


def test_fetch_uses_one_request_per_board(fake_http):
    greenhouse.fetch("examplebio", "Example Bio", fake_http)
    assert len(fake_http.calls) == 1


class _Resp:
    def __init__(self, status, data=None, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}

    def json(self):
        return self._data


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}

    def get(self, *a, **k):
        return self.responses.pop(0)


def test_http_retries_then_succeeds_and_honours_retry_after():
    sleeps = []
    http = Http(session=_Session([_Resp(429, headers={"Retry-After": "3"}), _Resp(503), _Resp(200, [1])]),
                sleep=sleeps.append)
    assert http.get_json("https://x") == [1]
    assert sleeps == [3.0, 4.0]


def test_http_404_and_giving_up():
    import pytest

    with pytest.raises(NotFound):
        Http(session=_Session([_Resp(404)]), sleep=lambda s: None).get_json("https://x")
    with pytest.raises(SourceError):
        Http(retries=1, session=_Session([_Resp(500), _Resp(500)]), sleep=lambda s: None).get_json("https://x")
    with pytest.raises(SourceError):  # 403 is not retried
        Http(session=_Session([_Resp(403)]), sleep=lambda s: None).get_json("https://x")
