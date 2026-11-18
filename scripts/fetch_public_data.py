#!/usr/bin/env python3
"""Download the attributed UCI source, then prepare bounded, reproducible CSV batches."""

import argparse
import csv
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
URL = "https://archive.ics.uci.edu/static/public/352/online+retail.zip"
FIELDS = [
    "invoice_no",
    "stock_code",
    "description",
    "quantity",
    "invoice_at",
    "unit_price",
    "currency",
    "country",
    "source_row",
]


def prepare(limit=5000, batch_rows=50000):
    raw = ROOT / "data" / "raw"
    prepared = ROOT / "data" / "prepared"
    raw.mkdir(parents=True, exist_ok=True)
    prepared.mkdir(parents=True, exist_ok=True)
    archive = raw / "online-retail.zip"
    if not archive.exists():
        temporary = archive.with_suffix(".download")
        with (
            urllib.request.urlopen(URL, timeout=60) as response,
            temporary.open("wb") as out,
        ):
            shutil.copyfileobj(response, out, length=65536)
        temporary.replace(archive)
    digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
    manifest_path = ROOT / "data" / "public_manifest.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if old.get("sha256") and old["sha256"] != digest:
        raise RuntimeError("官方文件摘要与已锁定版本不一致，请先核对来源，不能静默更新")
    workbook_path = raw / "Online Retail.xlsx"
    if not workbook_path.exists():
        with zipfile.ZipFile(archive) as z:
            with (
                z.open("Online Retail.xlsx") as source,
                workbook_path.open("wb") as target,
            ):
                shutil.copyfileobj(source, target, length=65536)
    workbook = load_workbook(
        workbook_path, read_only=True, data_only=True, keep_links=False
    )
    output = None
    accepted = rejected = 0
    total_amount = Decimal(0)
    total_quantity = cancelled = 0
    batches = []
    try:
        iterator = workbook.worksheets[0].iter_rows(values_only=True)
        header = next(iterator)
        expected = (
            "InvoiceNo",
            "StockCode",
            "Description",
            "Quantity",
            "InvoiceDate",
            "UnitPrice",
            "CustomerID",
            "Country",
        )
        if header != expected:
            raise RuntimeError("官方字段发生变化，请审查适配器")
        with (prepared / "preparation-errors.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as errors:
            error_writer = csv.writer(errors)
            error_writer.writerow(["source_row", "reason"])
            for row_number, row in enumerate(iterator, 2):
                if limit and accepted >= limit:
                    break
                (
                    invoice,
                    stock,
                    description,
                    qty,
                    date,
                    unit_price,
                    _customer,
                    country,
                ) = row
                try:
                    price = Decimal(str(unit_price))
                    if (
                        price < 0
                        or not isinstance(date, datetime)
                        or qty is None
                        or invoice is None
                        or stock is None
                    ):
                        raise ValueError("负价格或关键字段缺失")
                    quantity = int(qty)
                    if quantity != qty:
                        raise ValueError("数量非整数")
                    value = {
                        "invoice_no": str(invoice),
                        "stock_code": str(stock),
                        "description": description or "",
                        "quantity": quantity,
                        "invoice_at": date.replace(
                            tzinfo=ZoneInfo("Europe/London")
                        ).isoformat(),
                        "unit_price": format(price, "f"),
                        "currency": "GBP",
                        "country": country or "",
                        "source_row": row_number,
                    }
                except (ValueError, TypeError) as exc:
                    rejected += 1
                    error_writer.writerow([row_number, str(exc)])
                    continue
                if accepted % batch_rows == 0:
                    if output:
                        output.close()
                    path = prepared / f"uci-{accepted // batch_rows + 1:03}.csv"
                    batches.append({"file": path.name, "rows": 0})
                    output = path.open("w", encoding="utf-8-sig", newline="")
                    writer = csv.DictWriter(output, fieldnames=FIELDS)
                    writer.writeheader()
                writer.writerow(value)
                accepted += 1
                batches[-1]["rows"] += 1
                total_quantity += quantity
                total_amount += quantity * price
                cancelled += int(str(invoice).upper().startswith("C") or quantity < 0)
    finally:
        if output:
            output.close()
        workbook.close()
    for batch in batches:
        path = prepared / batch["file"]
        batch["sha256"] = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
    manifest = {
        "dataset": "UCI Online Retail",
        "creator": "Daqing Chen",
        "year": 2015,
        "doi": "10.24432/C5BW33",
        "source": "https://archive.ics.uci.edu/dataset/352/online+retail",
        "download_url": URL,
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "sha256": digest,
        "prepared_at": datetime.now(UTC).isoformat(),
        "currency": "GBP",
        "source_timezone": "Europe/London",
        "accepted": accepted,
        "rejected": rejected,
        "quantity": total_quantity,
        "amount": str(total_amount),
        "unmatched_cancellations": cancelled,
        "customer_identifiers": "not retained",
        "batches": batches,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "accepted": accepted,
                "rejected": rejected,
                "quantity": total_quantity,
                "amount_gbp": str(total_amount),
                "sha256": digest,
                "batches": len(batches),
            },
            ensure_ascii=False,
        )
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit", type=int, default=5000, help="0 prepares the entire dataset"
    )
    parser.add_argument("--batch-rows", type=int, default=50000)
    args = parser.parse_args()
    if args.limit < 0 or not 1 <= args.batch_rows <= 100000:
        parser.error("limit must be nonnegative and batch-rows must be 1–100000")
    prepare(args.limit, args.batch_rows)
