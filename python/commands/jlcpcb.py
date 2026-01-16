"""JLCPCB API client for fetching parts data.

Handles authentication and downloading the JLCPCB parts library
for integration with KiCAD component selection.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import string
import time
from collections.abc import Callable
from typing import Any

import requests

logger = logging.getLogger("kicad_interface")


class JLCPCBAPIError(Exception):
    """Error raised when JLCPCB API operations fail."""

    def __init__(self, message: str) -> None:
        """Initialize the error with a message.

        Args:
            message: Error description.
        """
        super().__init__(message)


class JLCPCBCredentialsError(JLCPCBAPIError):
    """Error raised when JLCPCB credentials are not configured."""


class JLCPCBClient:
    """Client for JLCPCB API.

    Handles HMAC-SHA256 signature-based authentication and fetching
    the complete parts library from JLCPCB's external API.
    """

    BASE_URL = "https://jlcpcb.com/external"

    def __init__(
        self,
        app_id: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        """Initialize JLCPCB API client.

        Args:
            app_id: JLCPCB App ID (or reads from JLCPCB_APP_ID env var)
            access_key: JLCPCB Access Key (or reads from JLCPCB_API_KEY env var)
            secret_key: JLCPCB Secret Key (or reads from JLCPCB_API_SECRET env var)
        """
        self.app_id = app_id or os.getenv("JLCPCB_APP_ID")
        self.access_key = access_key or os.getenv("JLCPCB_API_KEY")
        self.secret_key = secret_key or os.getenv("JLCPCB_API_SECRET")

        if not self.app_id or not self.access_key or not self.secret_key:
            logger.warning(
                "JLCPCB API credentials not found. "
                "Set JLCPCB_APP_ID, JLCPCB_API_KEY, and JLCPCB_API_SECRET environment variables."
            )

    @staticmethod
    def _generate_nonce() -> str:
        """Generate a 32-character random nonce.

        Returns:
            A 32-character random alphanumeric string.
        """
        chars = string.ascii_letters + string.digits
        return "".join(secrets.choice(chars) for _ in range(32))

    def _build_signature_string(
        self, method: str, path: str, timestamp: int, nonce: str, body: str
    ) -> str:
        r"""Build the signature string according to JLCPCB spec.

        Format:
        <HTTP Method>\n
        <Request Path>\n
        <Timestamp>\n
        <Nonce>\n
        <Request Body>\n

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Request path with query params
            timestamp: Unix timestamp in seconds
            nonce: 32-character random string
            body: Request body (empty string for GET)

        Returns:
            Signature string
        """
        return f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n"

    def _sign(self, signature_string: str) -> str:
        """Sign the signature string with HMAC-SHA256.

        Args:
            signature_string: The string to sign

        Returns:
            Base64-encoded signature
        """
        if self.secret_key is None:
            msg = "Secret key is not configured"
            raise JLCPCBCredentialsError(msg)

        signature_bytes = hmac.new(
            self.secret_key.encode("utf-8"), signature_string.encode("utf-8"), hashlib.sha256
        ).digest()
        return base64.b64encode(signature_bytes).decode("utf-8")

    def _get_auth_header(self, method: str, path: str, body: str = "") -> str:
        """Generate the Authorization header for JLCPCB API requests.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Request path with query params
            body: Request body JSON string (empty for GET)

        Returns:
            Authorization header value

        Raises:
            JLCPCBCredentialsError: If credentials are not configured.
        """
        if not self.app_id or not self.access_key or not self.secret_key:
            msg = (
                "JLCPCB API credentials not configured. "
                "Please set JLCPCB_APP_ID, JLCPCB_API_KEY, and JLCPCB_API_SECRET environment "
                "variables."
            )
            raise JLCPCBCredentialsError(
                msg
            )

        nonce = self._generate_nonce()
        timestamp = int(time.time())

        signature_string = self._build_signature_string(method, path, timestamp, nonce, body)
        signature = self._sign(signature_string)

        logger.debug("Signature string: %r", signature_string)
        logger.debug("Signature: %s", signature)
        logger.debug(
            'Auth header: JOP appid="%s",accesskey="%s",nonce="%s",timestamp="%s",signature="%s"',
            self.app_id,
            self.access_key,
            nonce,
            timestamp,
            signature,
        )

        return (
            f'JOP appid="{self.app_id}",accesskey="{self.access_key}",'
            f'nonce="{nonce}",timestamp="{timestamp}",signature="{signature}"'
        )

    def fetch_parts_page(self, last_key: str | None = None) -> dict[str, Any]:
        """Fetch one page of parts from JLCPCB API.

        Args:
            last_key: Pagination key from previous response (None for first page)

        Returns:
            Response dict with parts data and pagination info

        Raises:
            JLCPCBAPIError: If the API request fails.
        """
        path = "/component/getComponentInfos"

        payload: dict[str, str] = {}
        if last_key:
            payload["lastKey"] = last_key

        # Convert payload to JSON string for signing
        # For POST requests, we always send JSON, even if empty dict
        body_str = json.dumps(payload, separators=(",", ":"))

        # Generate authorization header
        auth_header = self._get_auth_header("POST", path, body_str)

        headers = {"Authorization": auth_header, "Content-Type": "application/json"}

        try:
            response = requests.post(
                f"{self.BASE_URL}{path}", headers=headers, json=payload, timeout=60
            )

            logger.debug("Response status: %s", response.status_code)
            logger.debug("Response headers: %s", response.headers)
            logger.debug("Response text: %s", response.text)

            response.raise_for_status()
            data: dict[str, Any] = response.json()

            if data.get("code") != 200:
                msg = (
                    f"API request failed (code {data.get('code')}): "
                    f"{data.get('msg', 'Unknown error')} - Full response: {data}"
                )
                raise JLCPCBAPIError(
                    msg
                )

            return data["data"]

        except requests.exceptions.RequestException as e:
            logger.exception("Failed to fetch parts page: %s", e)
            msg = f"JLCPCB API request failed: {e}"
            raise JLCPCBAPIError(msg) from e

    def download_full_database(
        self, callback: Callable[[int, int, str], None] | None = None
    ) -> list[dict[str, Any]]:
        """Download entire parts library from JLCPCB.

        Args:
            callback: Optional progress callback function(current_page, total_parts, status_msg)

        Returns:
            List of all parts

        Raises:
            JLCPCBAPIError: If the API request fails and no parts were downloaded.
        """
        all_parts: list[dict[str, Any]] = []
        last_key: str | None = None
        page = 0

        logger.info("Starting full JLCPCB parts database download...")

        while True:
            page += 1

            try:
                data = self.fetch_parts_page(last_key)

                parts: list[dict[str, Any]] = data.get("componentInfos", [])
                all_parts.extend(parts)

                last_key = data.get("lastKey")

                if callback:
                    callback(page, len(all_parts), f"Downloaded {len(all_parts)} parts...")
                else:
                    logger.info("Page %d: Downloaded %d parts so far...", page, len(all_parts))

                # Check if there are more pages
                if not last_key or len(parts) == 0:
                    break

                # Rate limiting - be nice to the API
                time.sleep(0.5)

            except JLCPCBAPIError:
                logger.exception("Error downloading parts at page %d", page)
                if len(all_parts) > 0:
                    logger.warning("Partial download available: %d parts", len(all_parts))
                    return all_parts
                raise

        logger.info("Download complete: %d parts retrieved", len(all_parts))
        return all_parts

    def get_part_by_lcsc(self, lcsc_number: str) -> dict[str, Any] | None:
        """Get detailed information for a specific LCSC part number.

        Note: This uses the same endpoint as fetching parts, as JLCPCB doesn't
        have a dedicated single-part endpoint. In practice, you should use
        the local database after initial download.

        Args:
            lcsc_number: LCSC part number (e.g., "C25804")

        Returns:
            Part info dict or None if not found
        """
        # For now, this would require searching through pages
        # In practice, you'd use the local database
        _ = lcsc_number  # Mark as intentionally unused
        logger.warning("get_part_by_lcsc should use local database, not API")
        return None


def test_jlcpcb_connection(
    app_id: str | None = None, access_key: str | None = None, secret_key: str | None = None
) -> bool:
    """Test JLCPCB API connection.

    Args:
        app_id: Optional App ID (uses env var if not provided)
        access_key: Optional Access Key (uses env var if not provided)
        secret_key: Optional Secret Key (uses env var if not provided)

    Returns:
        True if connection successful, False otherwise
    """
    try:
        client = JLCPCBClient(app_id, access_key, secret_key)
        # Test by fetching first page
        client.fetch_parts_page()
        logger.info("JLCPCB API connection test successful")
    except JLCPCBAPIError:
        logger.exception("JLCPCB API connection test failed")
        return False
    else:
        return True


if __name__ == "__main__":
    # Test the JLCPCB client
    logging.basicConfig(level=logging.INFO)

    if test_jlcpcb_connection():
        jlcpcb_client = JLCPCBClient()
        response_data = jlcpcb_client.fetch_parts_page()
        component_parts = response_data.get("componentInfos", [])

        if component_parts:
            first_part = component_parts[0]
            logger.info("First part: %s", first_part)
