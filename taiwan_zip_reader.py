#!/usr/bin/env python3
"""Resolve Taiwan addresses from 3, 5, or 6-digit postal codes.

CSV format (header required):
- required columns: zip,address

Example rows:
zip,address
100,台北市中正區
10058,台北市中正區忠孝西路一段
100581,台北市中正區忠孝西路一段XX號
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ZipRecord:
    zip_code: str
    address: str


@dataclass(frozen=True)
class LookupResult:
    zip_code: str
    address: str
    provider: str
    source: str


class ZipProvider(Protocol):
    name: str

    def lookup(self, zip_code: str, limit: int = 20) -> List[LookupResult]:
        ...


class TaiwanZipReader:
    def __init__(self, records: List[ZipRecord]) -> None:
        self.by_zip6: Dict[str, List[ZipRecord]] = {}
        self.by_zip5: Dict[str, List[ZipRecord]] = {}
        self.by_zip3: Dict[str, List[ZipRecord]] = {}

        for record in records:
            code = normalize_zip(record.zip_code)
            if len(code) not in {3, 5, 6}:
                continue

            if len(code) == 6:
                self.by_zip6.setdefault(code, []).append(record)
                self.by_zip5.setdefault(code[:5], []).append(record)
                self.by_zip3.setdefault(code[:3], []).append(record)
            elif len(code) == 5:
                self.by_zip5.setdefault(code, []).append(record)
                self.by_zip3.setdefault(code[:3], []).append(record)
            else:
                self.by_zip3.setdefault(code, []).append(record)

    @classmethod
    def from_csv(cls, csv_path: str | Path) -> "TaiwanZipReader":
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")

        records: List[ZipRecord] = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            required = {"zip", "address"}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                raise ValueError("CSV must include headers: zip,address")

            for row in reader:
                zip_code = normalize_zip(str(row.get("zip", "")))
                address = str(row.get("address", "")).strip()
                if not zip_code or not address:
                    continue
                records.append(ZipRecord(zip_code=zip_code, address=address))

        return cls(records)

    def lookup(self, zip_code: str, limit: int = 20) -> List[ZipRecord]:
        code = normalize_zip(zip_code)
        if len(code) == 6:
            return self.by_zip6.get(code, [])[:limit]
        if len(code) == 5:
            return self.by_zip5.get(code, [])[:limit]
        if len(code) == 3:
            return self.by_zip3.get(code, [])[:limit]
        raise ValueError("Zip code must be 3, 5, or 6 digits")


class LocalCsvProvider:
    name = "local_csv"

    def __init__(self, reader: TaiwanZipReader) -> None:
        self.reader = reader

    def lookup(self, zip_code: str, limit: int = 20) -> List[LookupResult]:
        rows = self.reader.lookup(zip_code, limit=limit)
        return [
            LookupResult(
                zip_code=row.zip_code,
                address=row.address,
                provider=self.name,
                source="local",
            )
            for row in rows
        ]


class NominatimProvider:
    name = "nominatim"
    endpoint = "https://nominatim.openstreetmap.org/search"

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

    def lookup(self, zip_code: str, limit: int = 20) -> List[LookupResult]:
        code = normalize_zip(zip_code)
        if len(code) not in {3, 5, 6}:
            raise ValueError("Zip code must be 3, 5, or 6 digits")

        params = {
            "postalcode": code,
            "countrycodes": "tw",
            "format": "jsonv2",
            "addressdetails": "1",
            "limit": str(limit),
        }
        url = f"{self.endpoint}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                # Nominatim usage policy requires a descriptive User-Agent.
                "User-Agent": "taiwan-zip-reader/1.0 (zipcode lookup utility)",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (URLError, HTTPError) as exc:
            raise RuntimeError(f"Nominatim request failed: {exc}") from exc

        results: List[LookupResult] = []
        for row in payload:
            display_name = str(row.get("display_name", "")).strip()
            if not display_name:
                continue
            results.append(
                LookupResult(
                    zip_code=code,
                    address=display_name,
                    provider=self.name,
                    source="api",
                )
            )

        return results


class LookupService:
    def __init__(self, primary: ZipProvider, fallback: ZipProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    def lookup(self, zip_code: str, limit: int = 20) -> List[LookupResult]:
        primary_results = self.primary.lookup(zip_code, limit=limit)
        if primary_results:
            return primary_results

        if self.fallback is None:
            return []

        return self.fallback.lookup(zip_code, limit=limit)


def normalize_zip(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Taiwan ZIP to address reader")
    parser.add_argument("zip_code", help="3, 5, or 6-digit Taiwan postal code")
    parser.add_argument("--csv", help="Path to CSV file with zip,address", default="data/tw_zip_sample.csv")
    parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument(
        "--provider",
        choices=["auto", "local", "nominatim"],
        default="auto",
        help="Lookup provider. auto=local with nominatim fallback (default: auto)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="HTTP timeout in seconds for API provider (default: 10)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print results in JSON format",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    provider: ZipProvider
    fallback: ZipProvider | None = None

    if args.provider in {"auto", "local"}:
        if not args.csv:
            raise ValueError("--csv is required when provider is 'auto' or 'local'")
        local = LocalCsvProvider(TaiwanZipReader.from_csv(args.csv))
        if args.provider == "local":
            provider = local
        else:
            provider = local
            fallback = NominatimProvider(timeout=args.timeout)
    else:
        provider = NominatimProvider(timeout=args.timeout)

    service = LookupService(primary=provider, fallback=fallback)
    results = service.lookup(args.zip_code, limit=args.limit)

    if args.json:
        payload = [
            {
                "zip": r.zip_code,
                "address": r.address,
                "provider": r.provider,
                "source": r.source,
            }
            for r in results
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not results:
        print("No matched address")
        return

    for idx, row in enumerate(results, start=1):
        print(f"{idx:02d}. [{row.provider}/{row.source}] {row.zip_code} {row.address}")


if __name__ == "__main__":
    main()
