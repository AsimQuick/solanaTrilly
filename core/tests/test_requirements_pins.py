# ---
# module: core.tests.test_requirements_pins
# sprint: sprint-1
# story: US-1 AC-1.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-14
# dependencies: packaging
# ---
"""
Tests that AC-1.2 requirements are satisfied:
- All required packages are present in requirements.txt
- Each required package has explicit lower AND upper version bounds
- Django major version is 5
- Pydantic major version is 2
"""

import pathlib

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIREMENTS_FILE = pathlib.Path(__file__).resolve().parents[3] / "requirements.txt"

# Packages required by AC-1.2
AC_1_2_PACKAGES = [
    "django",
    "djangorestframework",
    "channels",
    "channels-redis",
    "celery",
    "redis",
    "daphne",
    "pydantic",
    "psycopg",
]


def _parse_requirements() -> dict[str, Requirement]:
    """
    Read requirements.txt and return a dict mapping canonical package name
    (lowercase, dashes normalised) to a Requirement object.

    Skips blank lines and pure comment lines. Strips inline comments before
    parsing so that `packaging` does not choke on them.
    """
    reqs: dict[str, Requirement] = {}
    text = REQUIREMENTS_FILE.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        # Skip blank lines and pure comment lines
        if not line or line.startswith("#"):
            continue
        # Strip inline comment (e.g. "Django>=5.2,<5.3   # comment")
        line = line.split(" #")[0].strip()
        if not line:
            continue
        req = Requirement(line)
        key = canonicalize_name(req.name)
        reqs[key] = req
    return reqs


def has_lower_bound(spec_set: SpecifierSet) -> bool:
    return any(s.operator in (">=", ">", "==") for s in spec_set)


def has_upper_bound(spec_set: SpecifierSet) -> bool:
    return any(s.operator in ("<", "<=", "==") for s in spec_set)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def parsed_reqs() -> dict[str, Requirement]:
    return _parse_requirements()


@pytest.fixture(scope="module")
def canonical_required() -> list[str]:
    return [canonicalize_name(p) for p in AC_1_2_PACKAGES]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_required_packages_present(parsed_reqs, canonical_required):
    """All 9 AC-1.2 packages must appear in requirements.txt."""
    missing = [pkg for pkg in canonical_required if pkg not in parsed_reqs]
    assert not missing, (
        f"The following required packages are missing from requirements.txt: {missing}"
    )


def test_all_required_packages_have_lower_bound(parsed_reqs, canonical_required):
    """Every required package must have a >= , >, or == specifier."""
    violations = []
    for pkg in canonical_required:
        req = parsed_reqs.get(pkg)
        if req is None or not has_lower_bound(req.specifier):
            violations.append(pkg)
    assert not violations, (
        f"Packages missing a lower version bound: {violations}"
    )


def test_all_required_packages_have_upper_bound(parsed_reqs, canonical_required):
    """Every required package must have a < , <=, or == specifier."""
    violations = []
    for pkg in canonical_required:
        req = parsed_reqs.get(pkg)
        if req is None or not has_upper_bound(req.specifier):
            violations.append(pkg)
    assert not violations, (
        f"Packages missing an upper version bound: {violations}"
    )


def test_django_major_version_5(parsed_reqs):
    """Django's lower bound must be >= 5.x (not 4.x or older)."""
    django_req = parsed_reqs.get(canonicalize_name("django"))
    assert django_req is not None, "django not found in requirements.txt"
    lower_specs = [s for s in django_req.specifier if s.operator in (">=", ">", "==")]
    assert lower_specs, "django has no lower-bound specifier"
    # All lower-bound specifiers should have major == 5
    for spec in lower_specs:
        major = int(spec.version.split(".")[0])
        assert major == 5, (
            f"Django lower bound '{spec}' has major version {major}, expected 5"
        )


def test_pydantic_major_version_2(parsed_reqs):
    """Pydantic's lower bound must be >= 2.x (Pydantic v2, not v1)."""
    pydantic_req = parsed_reqs.get(canonicalize_name("pydantic"))
    assert pydantic_req is not None, "pydantic not found in requirements.txt"
    lower_specs = [s for s in pydantic_req.specifier if s.operator in (">=", ">", "==")]
    assert lower_specs, "pydantic has no lower-bound specifier"
    # All lower-bound specifiers should have major == 2
    for spec in lower_specs:
        major = int(spec.version.split(".")[0])
        assert major == 2, (
            f"Pydantic lower bound '{spec}' has major version {major}, expected 2"
        )
