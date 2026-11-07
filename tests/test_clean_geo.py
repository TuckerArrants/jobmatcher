import pytest

from jobmatcher.clean import (
    clearance_required,
    html_to_text,
    parse_salary,
    parse_years_required,
    strip_boilerplate,
)
from jobmatcher.geo import haversine_miles, parse_location, zip_coords


def test_html_to_text_keeps_structure():
    text = html_to_text("<p>One</p><ul><li>a</li><li>b</li></ul><script>x()</script>")
    assert text == "One\n\n- a\n\n- b"


def test_strip_boilerplate_drops_sections_and_sentences():
    text = "About\nGreat job.\n\nBenefits\n- dental\n\nRequirements\n- Python\nWe are an equal opportunity employer."
    out = strip_boilerplate(text)
    assert "dental" not in out and "Python" in out and "equal opportunity" not in out


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Salary: $120,000 - $150,000 per year", (120000, 150000)),
        ("$120k–$150k", (120000, 150000)),
        ("$45 - $60 per hour", (45 * 2080, 60 * 2080)),
        ("We raised $50 - $80 million", (None, None)),  # implausible as a salary
        ("no numbers here", (None, None)),
    ],
)
def test_parse_salary(text, expected):
    assert parse_salary(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("5+ years of experience in Python", 5),
        ("3-5 years of relevant industry experience; 1+ years of SQL experience", 1),
        ("At least 7 years experience", 7),
        ("founded 10 years ago", None),
        ("", None),
    ],
)
def test_parse_years_required(text, expected):
    assert parse_years_required(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Active TS/SCI clearance required", "top_secret"),
        ("Must obtain a Secret clearance", "secret"),
        ("Ability to obtain Public Trust", "public_trust"),
        ("Experience with secrets management and trade secret law", None),
    ],
)
def test_clearance_required(text, expected):
    assert clearance_required(text) == expected


def test_zip_and_distance():
    boulder = zip_coords("80302")
    denver = zip_coords("80202")
    assert boulder and denver
    assert 20 < haversine_miles(boulder, denver) < 30
    assert zip_coords("00000") is None


@pytest.mark.parametrize(
    "text, remote, country, state, located",
    [
        ("Boston, MA", False, "US", "MA", True),
        ("Remote - US", True, "US", None, False),
        ("Remote", True, None, None, False),
        ("London, UK", False, "foreign", None, False),
        ("Toronto, ON", False, "foreign", None, False),
        ("Remote - Canada", True, "foreign", None, False),
        ("NYC", False, "US", "NY", True),
        ("Saint Louis, Missouri", False, "US", "MO", True),
        ("Hybrid - Nashville, TN", False, "US", "TN", True),
        ("Remote - Texas", True, "US", "TX", False),
        ("Portland, OR", False, "US", "OR", True),
        ("United States", False, "US", None, False),
    ],
)
def test_parse_location(text, remote, country, state, located):
    p = parse_location(text)
    assert (p.remote, p.country, p.state, p.located) == (remote, country, state, located)
