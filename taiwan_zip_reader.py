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
import os
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


class GoogleMapsProvider:
    name = "google_maps"
    endpoint = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(self, api_key: str, timeout: int = 10) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def lookup(self, zip_code: str, limit: int = 20) -> List[LookupResult]:
        code = normalize_zip(zip_code)
        if len(code) not in {3, 5, 6}:
            raise ValueError("Zip code must be 3, 5, or 6 digits")

        params = {
            "address": code,
            "components": f"country:TW|postal_code:{code}",
            "region": "tw",
            "language": "zh-TW",
            "key": self.api_key,
        }
        url = f"{self.endpoint}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "User-Agent": "taiwan-zip-reader/1.0 (zipcode lookup utility)",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (URLError, HTTPError) as exc:
            raise RuntimeError(f"Google Maps request failed: {exc}") from exc

        status = str(payload.get("status", ""))
        if status == "ZERO_RESULTS":
            return []
        if status != "OK":
            err = str(payload.get("error_message", "")).strip()
            detail = f": {err}" if err else ""
            raise RuntimeError(f"Google Maps API error: {status}{detail}")

        rows = payload.get("results", [])
        results: List[LookupResult] = []
        for row in rows[:limit]:
            formatted = str(row.get("formatted_address", "")).strip()
            if not formatted:
                continue
            results.append(
                LookupResult(
                    zip_code=code,
                    address=formatted,
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
    parser.add_argument(
        "zip_codes",
        nargs="+",
        help="One or more 3, 5, or 6-digit Taiwan postal codes",
    )
    parser.add_argument("--csv", help="Path to CSV file with zip,address", default="data/tw_zip_sample.csv")
    parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument(
        "--provider",
        choices=["auto", "local", "nominatim", "google"],
        default="auto",
        help="Lookup provider. auto=local with nominatim fallback (default: auto)",
    )
    parser.add_argument(
        "--google-api-key",
        help="Google Maps API key (or set GOOGLE_MAPS_API_KEY env var)",
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
    elif args.provider == "nominatim":
        provider = NominatimProvider(timeout=args.timeout)
    elif args.provider == "google":
        api_key = args.google_api_key or os.getenv("GOOGLE_MAPS_API_KEY", "")
        if not api_key:
            raise ValueError("Google provider requires --google-api-key or GOOGLE_MAPS_API_KEY")
        print("I am going to use Google Maps API")
        provider = GoogleMapsProvider(api_key=api_key, timeout=args.timeout)
    else:
        raise ValueError(f"Unknown provider: {args.provider}")

    service = LookupService(primary=provider, fallback=fallback)
    grouped_results: List[tuple[str, List[LookupResult]]] = []
    for zip_code in args.zip_codes:
        grouped_results.append((zip_code, service.lookup(zip_code, limit=args.limit)))

    if args.json:
        payload = [
            {
                "input_zip": query_zip,
                "results": [
                    {
                        "zip": r.zip_code,
                        "address": r.address,
                        "provider": r.provider,
                        "source": r.source,
                    }
                    for r in rows
                ],
            }
            for query_zip, rows in grouped_results
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not any(rows for _, rows in grouped_results):
        print("No matched address")
        return

    show_heading = len(grouped_results) > 1
    for query_zip, rows in grouped_results:
        if show_heading:
            print(f"Input ZIP: {query_zip}")

        if not rows:
            print("No matched address")
        else:
            for idx, row in enumerate(rows, start=1):
                print(f"{idx:02d}. [{row.provider}/{row.source}] {row.zip_code} {row.address}")

        if show_heading:
            print()


if __name__ == "__main__":
    main()
