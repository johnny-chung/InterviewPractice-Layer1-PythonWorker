import os
import re
import pytest
import requests

from utils.onet_client import (
    is_enabled,
    search_onet_code,
    fetch_onet_skills,
    search_onet_codes_multi,
    ONET_ENDPOINT,
)


pytestmark = [pytest.mark.integration]


@pytest.mark.skipif(not is_enabled(), reason="ONET credentials not configured in environment")
def test_onet_client_enabled_via_env():
    assert is_enabled() is True


@pytest.mark.skipif(not is_enabled(), reason="ONET credentials not configured in environment")
def test_onet_about_connectivity():
    # Validate connectivity & credentials via the /ws/about/ JSON endpoint
    user = os.getenv("ONET_USER")
    password = os.getenv("ONET_PASSWORD")
    url = "https://services.onetcenter.org/ws/about/"
    resp = requests.get(
        url,
        auth=(user, password),
        headers={"Accept": "application/json", "User-Agent": "skill_search"},
        timeout=5,
    )
    assert resp.ok, f"About endpoint unreachable: HTTP {resp.status_code}"
    ct = resp.headers.get("Content-Type", "")
    assert "json" in ct.lower(), f"Expected JSON response, got {ct}"
    data = resp.json()
    assert isinstance(data, dict), "Expected JSON object from about endpoint"


@pytest.mark.skipif(not is_enabled(), reason="ONET credentials not configured in environment")
def test_search_onet_code_returns_code_for_common_title():
    # Use a common title that should resolve
    code = search_onet_code("Software Developer")
    assert code is not None, "Expected a SOC code from O*NET search"
    # Basic sanity check of SOC code formatting (e.g., 15-1252.00)
    assert re.match(r"^\d{2}-\d{4}\.\d{2}$", code), f"Unexpected SOC code format: {code}"


@pytest.mark.skipif(not is_enabled(), reason="ONET credentials not configured in environment")
def test_fetch_onet_skills_for_known_code():
    # Prefer a code from env if provided, otherwise fall back to Software Developers (example)
    env_codes = os.getenv("ONET_SKILL_CODES", "").split(",")
    code = (env_codes[0].strip() if env_codes and env_codes[0].strip() else "15-1252.00")

    skills = fetch_onet_skills(code)
    assert isinstance(skills, list)
    assert len(skills) > 0, f"Expected at least one skill for code {code}"
    # Validate shape of returned items
    for item in skills:
        assert set(["skill", "importance", "source"]).issubset(item.keys())
        assert item["source"] == "onet"
        assert 0.0 <= float(item["importance"]) <= 1.0


@pytest.mark.skipif(not is_enabled(), reason="ONET credentials not configured in environment")
def test_search_onet_codes_multi_single_full_title():
    # Using a multi-word title should only perform a single full-title query internally.
    # We cannot easily assert number of HTTP calls here without monkeypatching, so we
    # assert that we still obtain at least one valid SOC code and formatting is correct.
    codes = search_onet_codes_multi("Senior Software Engineer")
    assert isinstance(codes, list)
    assert len(codes) > 0, "Expected at least one SOC code from multi search"
    for code in codes:
        assert re.match(r"^\d{2}-\d{4}\.\d{2}$", code), f"Unexpected SOC code format: {code}"


