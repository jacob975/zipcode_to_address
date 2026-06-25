# Taiwan ZIP Address Reader (3 / 5 / 6 digits)

This project reads Taiwan addresses from:
- `3` digits (county/city + district level)
- `5` digits (legacy 3+2)
- `6` digits (new 3+3)

## 1) Prepare data
Use a CSV file with required headers:

```csv
zip,address
100,台北市中正區
10058,台北市中正區忠孝西路一段
100581,台北市中正區忠孝西路一段1號附近
```

A sample file is included at `data/tw_zip_sample.csv`.

## 2) Run

Default mode is `auto`:
- primary: local CSV
- fallback: Nominatim API (OpenStreetMap)

```bash
python3 taiwan_zip_reader.py 100
python3 taiwan_zip_reader.py 10058
python3 taiwan_zip_reader.py 100581
```

Provider modes:

```bash
# Local CSV only
python3 taiwan_zip_reader.py --provider local 100

# Auto: local first, fallback to Nominatim if no local match
python3 taiwan_zip_reader.py --provider auto 999

# Nominatim only (no CSV required)
python3 taiwan_zip_reader.py --provider nominatim 100
```

JSON output:

```bash
python3 taiwan_zip_reader.py 100 --json
```

JSON fields are unified across providers:
- `zip`
- `address`
- `provider`
- `source`

## 3) Where to get full Taiwan ZIP data
Use official open data from Chunghwa Post (Taiwan Post), then convert to `zip,address` CSV format. After replacing the sample CSV with full data, this tool works for production lookup scenarios.

## Notes
- Input can include separators/spaces; non-digit characters are removed automatically.
- Input must resolve to exactly `3`, `5`, or `6` digits.
- For 3-digit lookup, multiple addresses may be returned, so you can refine with 5/6 digits.
- The default provider is `--auto`.
- Nominatim is an external service; network availability and usage limits apply.
