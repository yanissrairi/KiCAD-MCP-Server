"""Integration tests for JLCPCB API client.

These tests make real API calls to JLCPCB and require valid credentials.
They will be skipped if credentials are not configured.

Required environment variables:
    JLCPCB_APP_ID: Your JLCPCB App ID
    JLCPCB_ACCESS_KEY: Your JLCPCB Access Key
    JLCPCB_SECRET_KEY: Your JLCPCB Secret Key

Usage:
    # Run all tests including integration tests
    pytest

    # Skip integration tests (for CI without credentials)
    pytest -m "not integration"

    # Run only integration tests
    pytest -m integration
"""

from __future__ import annotations

import os

import pytest


def has_jlcpcb_credentials() -> bool:
    """Check if JLCPCB API credentials are configured in environment.

    Returns:
        True if all required credentials are present, False otherwise.
    """
    return all([
        os.getenv("JLCPCB_APP_ID"),
        os.getenv("JLCPCB_ACCESS_KEY"),
        os.getenv("JLCPCB_SECRET_KEY"),
    ])


@pytest.mark.integration
@pytest.mark.skipif(
    not has_jlcpcb_credentials(),
    reason="JLCPCB credentials not configured (set JLCPCB_APP_ID, JLCPCB_ACCESS_KEY, JLCPCB_SECRET_KEY)",
)
def test_jlcpcb_api_connection() -> None:
    """Test real JLCPCB API connection with configured credentials.

    This is an integration test that makes actual API calls to JLCPCB.
    It validates that:
    1. The API client can be instantiated with credentials
    2. The API connection is successful
    3. The response structure is as expected

    Note:
        This test will be skipped if credentials are not configured.
        It may fail if JLCPCB API is down or rate limits are hit.
    """
    from commands.jlcpcb import JLCPCBClient

    # Create client with credentials from environment
    client = JLCPCBClient()

    # Fetch first page of parts - this should not raise if credentials are valid
    response = client.fetch_parts_page()

    # Validate response structure
    assert response is not None, "API response should not be None"
    assert isinstance(response, dict), "API response should be a dictionary"
    assert "componentInfos" in response, "Response should contain componentInfos key"

    # Validate that we got some data
    component_infos = response["componentInfos"]
    assert isinstance(component_infos, list), "componentInfos should be a list"
    # Note: We don't assert len > 0 because the API might return empty results
