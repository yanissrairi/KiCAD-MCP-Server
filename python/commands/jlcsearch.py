"""JLCSearch API client (public, no authentication required).

Alternative to official JLCPCB API using the community-maintained
jlcsearch service at https://jlcsearch.tscircuit.com/
"""

from collections.abc import Callable
import logging
import time
from typing import Any

import requests

logger = logging.getLogger("kicad_interface")


class JLCSearchClient:
    """Client for JLCSearch public API (tscircuit).

    Provides access to JLCPCB parts database without authentication
    via the community-maintained jlcsearch service.
    """

    BASE_URL = "https://jlcsearch.tscircuit.com"

    def __init__(self) -> None:
        """Initialize JLCSearch API client."""

    def search_components(
        self,
        category: str = "components",
        limit: int = 100,
        offset: int = 0,
        **filters: str | int | bool,
    ) -> list[dict[str, Any]]:
        """Search components in JLCSearch database.

        Args:
            category: Component category (e.g., "resistors", "capacitors", "components")
            limit: Maximum number of results
            offset: Offset for pagination
            **filters: Additional filters (e.g., package="0603", resistance=1000)

        Returns:
            List of component dicts
        """
        url = f"{self.BASE_URL}/{category}/list.json"

        params = {"limit": limit, "offset": offset, **filters}

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            # The response has the category name as key
            # e.g., {"resistors": [...]} or {"components": [...]}
            for value in data.values():
                if isinstance(value, list):
                    return value

            return []

        except requests.exceptions.RequestException:
            logger.exception("Failed to search JLCSearch")
            raise

    def search_resistors(
        self, resistance: int | None = None, package: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Search for resistors.

        Args:
            resistance: Resistance value in ohms
            package: Package type (e.g., "0603", "0805")
            limit: Maximum results

        Returns:
            List of resistor dicts with fields:
            - lcsc: LCSC number (integer)
            - mfr: Manufacturer part number
            - package: Package size
            - is_basic: True if basic library part
            - resistance: Resistance in ohms
            - tolerance_fraction: Tolerance (0.01 = 1%)
            - power_watts: Power rating in mW
            - stock: Available stock
            - price1: Price per unit
        """
        filters = {}
        if resistance is not None:
            filters["resistance"] = resistance
        if package:
            filters["package"] = package

        return self.search_components("resistors", limit=limit, **filters)

    def search_capacitors(
        self, capacitance: float | None = None, package: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Search for capacitors.

        Args:
            capacitance: Capacitance value in farads
            package: Package type
            limit: Maximum results

        Returns:
            List of capacitor dicts
        """
        filters = {}
        if capacitance is not None:
            filters["capacitance"] = capacitance
        if package:
            filters["package"] = package

        return self.search_components("capacitors", limit=limit, **filters)

    def get_part_by_lcsc(self, lcsc_number: int) -> dict | None:
        """Get part details by LCSC number.

        Args:
            lcsc_number: LCSC number (integer, without 'C' prefix)

        Returns:
            Part dict or None if not found
        """
        # Search across all components filtering by LCSC
        # Note: jlcsearch doesn't have a dedicated single-part endpoint
        # so we search and filter
        try:
            results = self.search_components("components", limit=1, lcsc=lcsc_number)
            return results[0] if results else None
        except Exception:
            logger.exception("Failed to get part C%s", lcsc_number)
            return None

    def download_all_components(
        self, callback: Callable[[int, str], None] | None = None, batch_size: int = 1000
    ) -> list[dict]:
        """Download all components from jlcsearch database.

        Args:
            callback: Optional progress callback function(parts_count, status_msg)
            batch_size: Number of parts per batch

        Returns:
            List of all parts
        """
        all_parts = []
        offset = 0

        logger.info("Starting full jlcsearch parts database download...")

        while True:
            try:
                batch = self.search_components("components", limit=batch_size, offset=offset)

                if not batch:
                    break

                all_parts.extend(batch)
                offset += len(batch)

                if callback:
                    callback(len(all_parts), f"Downloaded {len(all_parts)} parts...")
                else:
                    logger.info("Downloaded %d parts so far...", len(all_parts))

                # If we got fewer results than requested, we've reached the end
                if len(batch) < batch_size:
                    break

                # Rate limiting - be nice to the API
                time.sleep(0.1)

            except Exception:
                logger.exception("Error downloading parts at offset %d", offset)
                if len(all_parts) > 0:
                    logger.warning("Partial download available: %d parts", len(all_parts))
                    return all_parts
                raise

        logger.info("Download complete: %d parts retrieved", len(all_parts))
        return all_parts


def test_jlcsearch_connection() -> bool:
    """Test JLCSearch API connection.

    Returns:
        True if connection successful, False otherwise
    """
    try:
        client = JLCSearchClient()
        # Test by searching for 1k resistors
        results = client.search_resistors(resistance=1000, limit=5)
        logger.info(
            "JLCSearch API connection test successful - found %d resistors", len(results)
        )
        return True
    except Exception:
        logger.exception("JLCSearch API connection test failed")
        return False


if __name__ == "__main__":
    # Test the JLCSearch client
    logging.basicConfig(level=logging.INFO)

    if test_jlcsearch_connection():

        client = JLCSearchClient()

        resistors = client.search_resistors(resistance=1000, package="0603", limit=5)

        if resistors:
            r = resistors[0]
    else:
        pass
