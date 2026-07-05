"""
Supply-chain checks: ecosystem detection + typosquat matching.

These test the OFFLINE logic (no network) so they're deterministic in CI.
The live PyPI / Maven Central lookups in check_package are exercised end-to-end
by the demo, not here.  Run with:  pytest
"""

import tools


def test_detect_maven_coordinate():
    assert tools._detect_ecosystem("com.google.guava:guava") == "maven"
    assert tools._detect_ecosystem("org.apache.logging.log4j:log4j-core:2.14.1") == "maven"


def test_detect_maven_group_only():
    # reverse-domain group id, no version pin
    assert tools._detect_ecosystem("com.fasterxml.jackson.core") == "maven"


def test_detect_pypi():
    assert tools._detect_ecosystem("requests") == "pypi"
    assert tools._detect_ecosystem("reqests==2.31.0") == "pypi"


def test_maven_typosquat_detected():
    # 'jackson-databin' is one deletion away from the real 'jackson-databind'
    assert "jackson-databind" in tools._typosquat_of("jackson-databin", tools._POPULAR_MAVEN)


def test_pypi_typosquat_detected():
    assert "requests" in tools._typosquat_of("reqests", tools._POPULAR)


def test_legit_package_is_not_flagged():
    assert tools._typosquat_of("guava", tools._POPULAR_MAVEN) == []
    assert tools._typosquat_of("requests", tools._POPULAR) == []
