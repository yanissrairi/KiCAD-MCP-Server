"""JLCPCB Parts Database Manager.

Manages local SQLite database of JLCPCB parts for fast searching
and component selection.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("kicad_interface")


class JLCPCBPartsManager:
    """Manages local database of JLCPCB parts.

    Provides fast parametric search, filtering, and package-to-footprint mapping.
    """

    def __init__(self, db_path: str | None = None) -> None:
        """Initialize parts database manager.

        Args:
            db_path: Path to SQLite database file (default: data/jlcpcb_parts.db)
        """
        if db_path is None:
            # Default to data directory in project root
            project_root = Path(__file__).parent.parent.parent
            data_dir = project_root / "data"
            data_dir.mkdir(exist_ok=True)
            db_path = str(data_dir / "jlcpcb_parts.db")

        self.db_path = db_path
        self.conn = None
        self._init_database()

    def _init_database(self) -> None:
        """Initialize SQLite database with schema."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # Return rows as dicts

        cursor = self.conn.cursor()

        # Create components table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS components (
                lcsc TEXT PRIMARY KEY,
                category TEXT,
                subcategory TEXT,
                mfr_part TEXT,
                package TEXT,
                solder_joints INTEGER,
                manufacturer TEXT,
                library_type TEXT,
                description TEXT,
                datasheet TEXT,
                stock INTEGER,
                price_json TEXT,
                last_updated INTEGER
            )
        """)

        # Create indexes for fast searching
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_category ON components(category, subcategory)"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_package ON components(package)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_manufacturer ON components(manufacturer)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_type ON components(library_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mfr_part ON components(mfr_part)")

        # Full-text search index for descriptions
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS components_fts USING fts5(
                lcsc,
                description,
                mfr_part,
                manufacturer,
                content=components
            )
        """)

        self.conn.commit()
        logger.info("Initialized JLCPCB parts database at %s", self.db_path)

    def import_parts(
        self,
        parts: list[dict[str, Any]],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> None:
        """Import parts into database from JLCPCB API response.

        Args:
            parts: List of part dicts from JLCPCB API
            progress_callback: Optional callback(current, total, message)
        """
        cursor = self.conn.cursor()
        imported = 0
        skipped = 0

        for i, part in enumerate(parts):
            try:
                # Extract price breaks
                price_json = json.dumps(part.get("prices", []))

                # Determine library type
                library_type = self._determine_library_type(part)

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO components (
                        lcsc, category, subcategory, mfr_part, package,
                        solder_joints, manufacturer, library_type, description,
                        datasheet, stock, price_json, last_updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        part.get("componentCode"),  # lcsc
                        part.get("firstSortName"),  # category
                        part.get("secondSortName"),  # subcategory
                        part.get("componentModelEn"),  # mfr_part
                        part.get("componentSpecificationEn"),  # package
                        part.get("soldPoint"),  # solder_joints
                        part.get("componentBrandEn"),  # manufacturer
                        library_type,  # library_type
                        part.get("describe"),  # description
                        part.get("dataManualUrl"),  # datasheet
                        part.get("stockCount", 0),  # stock
                        price_json,  # price_json
                        int(datetime.now(tz=UTC).timestamp()),  # last_updated
                    ),
                )

                imported += 1

                if progress_callback and (i + 1) % 1000 == 0:
                    progress_callback(i + 1, len(parts), f"Imported {imported} parts...")

            except Exception:
                logger.exception("Error importing part %s", part.get("componentCode"))
                skipped += 1

        # Update FTS index
        cursor.execute("""
            INSERT INTO components_fts(components_fts, rowid, lcsc, description, mfr_part, manufacturer)
            SELECT 'rebuild', rowid, lcsc, description, mfr_part, manufacturer FROM components
        """)

        self.conn.commit()
        logger.info("Import complete: %d parts imported, %d skipped", imported, skipped)

    def _determine_library_type(self, part: dict[str, Any]) -> str:
        """Determine if part is Basic, Extended, or Preferred."""
        # JLCPCB API should provide this, but if not, we infer from assembly type
        assembly_type = part.get("assemblyType", "")

        if "Basic" in assembly_type or part.get("libraryType") == "base":
            return "Basic"
        if "Extended" in assembly_type:
            return "Extended"
        if "Prefer" in assembly_type:
            return "Preferred"
        return "Extended"  # Default to Extended

    @staticmethod
    def _normalize_lcsc_number(lcsc: Any) -> str:
        """Normalize LCSC number to string format with C prefix.

        Args:
            lcsc: LCSC number (may be int or string)

        Returns:
            LCSC number as string with C prefix (e.g., "C25804")
        """
        if isinstance(lcsc, int):
            return f"C{lcsc}"
        return str(lcsc) if lcsc else ""

    @staticmethod
    def _build_price_json(part: dict[str, Any]) -> str:
        """Build price JSON from JLCSearch part data.

        Args:
            part: Part dictionary from JLCSearch

        Returns:
            JSON string with price breaks
        """
        price = part.get("price") or part.get("price1")
        price_data = [{"qty": 1, "price": price}] if price else []
        return json.dumps(price_data)

    @staticmethod
    def _determine_library_type(part: dict[str, Any]) -> str:
        """Determine library type from JLCSearch part flags.

        Args:
            part: Part dictionary from JLCSearch

        Returns:
            Library type: "Basic", "Preferred", or "Extended"
        """
        if part.get("is_preferred"):
            return "Preferred"
        if part.get("is_basic"):
            return "Basic"
        return "Extended"

    @staticmethod
    def _build_description(part: dict[str, Any]) -> str:
        """Build description from JLCSearch part data.

        Args:
            part: Part dictionary from JLCSearch

        Returns:
            Description string
        """
        description_parts = []
        
        if "resistance" in part:
            description_parts.append(f"{part['resistance']}Ω")
        if "capacitance" in part:
            description_parts.append(f"{part['capacitance']}F")
        if "tolerance_fraction" in part:
            tol = part["tolerance_fraction"] * 100
            description_parts.append(f"±{tol}%")
        if "power_watts" in part:
            description_parts.append(f"{part['power_watts']}mW")
        if "voltage" in part:
            description_parts.append(f"{part['voltage']}V")

        return part.get("description", " ".join(description_parts))

    def import_jlcsearch_parts(
        self,
        parts: list[dict[str, Any]],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> None:
        """Import parts into database from JLCSearch API response.

        Args:
            parts: List of part dicts from JLCSearch API
            progress_callback: Optional callback(current, total, message)
        """
        cursor = self.conn.cursor()
        imported = 0
        skipped = 0

        for i, part in enumerate(parts):
            try:
                # Normalize and prepare part data
                lcsc = self._normalize_lcsc_number(part.get("lcsc"))
                price_json = self._build_price_json(part)
                library_type = self._determine_library_type(part)
                description = self._build_description(part)

                # Insert into database
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO components (
                        lcsc, category, subcategory, mfr_part, package,
                        solder_joints, manufacturer, library_type, description,
                        datasheet, stock, price_json, last_updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        lcsc,  # lcsc with C prefix
                        part.get("category", ""),  # category
                        part.get("subcategory", ""),  # subcategory
                        part.get("mfr", ""),  # mfr_part
                        part.get("package", ""),  # package
                        0,  # solder_joints (not in jlcsearch)
                        part.get("manufacturer", ""),  # manufacturer
                        library_type,  # library_type
                        description,  # description
                        "",  # datasheet (not in jlcsearch)
                        part.get("stock", 0),  # stock
                        price_json,  # price_json
                        int(datetime.now(tz=UTC).timestamp()),  # last_updated
                    ),
                )

                imported += 1

                if progress_callback and (i + 1) % 1000 == 0:
                    progress_callback(i + 1, len(parts), f"Imported {imported} parts...")

            except Exception:
                logger.exception("Error importing part %s", part.get("lcsc"))
                skipped += 1

        # Update FTS index
        cursor.execute("""
            INSERT INTO components_fts(components_fts)
            VALUES('rebuild')
        """)

        self.conn.commit()
        logger.info("Import complete: %d parts imported, %d skipped", imported, skipped)

    def search_parts(
        self,
        query: str | None = None,
        category: str | None = None,
        package: str | None = None,
        library_type: str | None = None,
        manufacturer: str | None = None,
        in_stock: bool = True,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search for parts with filters.

        Args:
            query: Free-text search (searches description, mfr part, LCSC)
            category: Filter by category name
            package: Filter by package type
            library_type: Filter by "Basic", "Extended", or "Preferred"
            manufacturer: Filter by manufacturer name
            in_stock: Only return parts with stock > 0
            limit: Maximum number of results

        Returns:
            List of matching parts
        """
        cursor = self.conn.cursor()

        # Build query
        sql_parts = ["SELECT * FROM components WHERE 1=1"]
        params = []

        if query:
            # Use FTS for text search
            sql_parts.append("""
                AND lcsc IN (
                    SELECT lcsc FROM components_fts
                    WHERE components_fts MATCH ?
                )
            """)
            params.append(query)

        if category:
            sql_parts.append("AND category LIKE ?")
            params.append(f"%{category}%")

        if package:
            sql_parts.append("AND package LIKE ?")
            params.append(f"%{package}%")

        if library_type:
            sql_parts.append("AND library_type = ?")
            params.append(library_type)

        if manufacturer:
            sql_parts.append("AND manufacturer LIKE ?")
            params.append(f"%{manufacturer}%")

        if in_stock:
            sql_parts.append("AND stock > 0")

        sql_parts.append("LIMIT ?")
        params.append(limit)

        sql = " ".join(sql_parts)

        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception:
            logger.exception("Search error")
            return []

    def get_part_info(self, lcsc_number: str) -> dict[str, Any] | None:
        """Get detailed information for specific LCSC part.

        Args:
            lcsc_number: LCSC part number (e.g., "C25804")

        Returns:
            Part info dict or None if not found
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM components WHERE lcsc = ?", (lcsc_number,))
        row = cursor.fetchone()

        if row:
            part = dict(row)
            # Parse price JSON
            if part.get("price_json"):
                try:
                    part["price_breaks"] = json.loads(part["price_json"])
                except json.JSONDecodeError:
                    part["price_breaks"] = []
            return part
        return None

    def get_database_stats(self) -> dict[str, Any]:
        """Get statistics about the database."""
        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) as total FROM components")
        total = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) as basic FROM components WHERE library_type = 'Basic'")
        basic = cursor.fetchone()["basic"]

        cursor.execute(
            "SELECT COUNT(*) as extended FROM components WHERE library_type = 'Extended'"
        )
        extended = cursor.fetchone()["extended"]

        cursor.execute("SELECT COUNT(*) as in_stock FROM components WHERE stock > 0")
        in_stock = cursor.fetchone()["in_stock"]

        return {
            "total_parts": total,
            "basic_parts": basic,
            "extended_parts": extended,
            "in_stock": in_stock,
            "db_path": self.db_path,
        }

    def map_package_to_footprint(self, package: str) -> list[str]:
        """Map JLCPCB package name to KiCAD footprint(s).

        Args:
            package: JLCPCB package name (e.g., "0603", "SOT-23")

        Returns:
            List of possible KiCAD footprint library refs
        """
        # Load mapping from JSON file or use defaults
        mappings = {
            "0402": [
                "Resistor_SMD:R_0402_1005Metric",
                "Capacitor_SMD:C_0402_1005Metric",
                "LED_SMD:LED_0402_1005Metric",
            ],
            "0603": [
                "Resistor_SMD:R_0603_1608Metric",
                "Capacitor_SMD:C_0603_1608Metric",
                "LED_SMD:LED_0603_1608Metric",
            ],
            "0805": ["Resistor_SMD:R_0805_2012Metric", "Capacitor_SMD:C_0805_2012Metric"],
            "1206": ["Resistor_SMD:R_1206_3216Metric", "Capacitor_SMD:C_1206_3216Metric"],
            "SOT-23": ["Package_TO_SOT_SMD:SOT-23", "Package_TO_SOT_SMD:SOT-23-3"],
            "SOT-23-5": ["Package_TO_SOT_SMD:SOT-23-5"],
            "SOT-23-6": ["Package_TO_SOT_SMD:SOT-23-6"],
            "SOIC-8": ["Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"],
            "SOIC-16": ["Package_SO:SOIC-16_3.9x9.9mm_P1.27mm"],
            "QFN-20": ["Package_DFN_QFN:QFN-20-1EP_4x4mm_P0.5mm_EP2.5x2.5mm"],
            "QFN-32": ["Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm"],
        }

        # Normalize package name
        package_normalized = package.strip().upper()

        for key, footprints in mappings.items():
            if key.upper() in package_normalized:
                return footprints

        return []

    def suggest_alternatives(self, lcsc_number: str, limit: int = 5) -> list[dict]:
        """Find alternative parts similar to the given LCSC number.

        Prioritizes: cheaper price, higher stock, Basic library type

        Args:
            lcsc_number: Reference LCSC part number
            limit: Maximum alternatives to return

        Returns:
            List of alternative parts
        """
        part = self.get_part_info(lcsc_number)
        if not part:
            return []

        # Search for parts in same category with same package
        alternatives = self.search_parts(
            category=part["subcategory"], package=part["package"], in_stock=True, limit=limit * 3
        )

        # Filter out the original part
        alternatives = [p for p in alternatives if p["lcsc"] != lcsc_number]

        # Sort by: Basic first, then by price, then by stock
        def sort_key(p: dict[str, Any]) -> tuple[int, float, int]:
            is_basic = 1 if p.get("library_type") == "Basic" else 0
            try:
                prices = json.loads(p.get("price_json", "[]"))
                price = float(prices[0].get("price", 999)) if prices else 999
            except (json.JSONDecodeError, IndexError, KeyError, TypeError, ValueError):
                price = 999
            stock = p.get("stock", 0)

            return (-is_basic, price, -stock)

        alternatives.sort(key=sort_key)

        return alternatives[:limit]

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()


if __name__ == "__main__":
    # Test the parts manager
    logging.basicConfig(level=logging.INFO)

    manager = JLCPCBPartsManager()

    # Get stats
    stats = manager.get_database_stats()

    if stats["total_parts"] > 0:
        results = manager.search_parts(query="10k resistor", limit=5)
        for _part in results:
            pass
