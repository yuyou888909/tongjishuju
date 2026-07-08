from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Inject a generated daily report into dist/data.")
    parser.add_argument("--report-date", default="", help="Report date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--reports-dir", default=str(ROOT / "reports-web"), help="Directory containing generated reports.")
    parser.add_argument("--dist-dir", default=str(ROOT / "dist"), help="Static site output directory.")
    args = parser.parse_args()

    report_date = parse_report_date(args.report_date)
    reports_dir = Path(args.reports_dir)
    dist_data = Path(args.dist_dir) / "data"
    report_json = reports_dir / f"{report_date}-morning.json"
    report_md = reports_dir / f"{report_date}-morning.md"

    if not report_json.exists() or not report_md.exists():
        raise FileNotFoundError(f"Missing generated report files for {report_date} in {reports_dir}")

    dist_data.mkdir(parents=True, exist_ok=True)
    result = json.loads(report_json.read_text(encoding="utf-8"))
    markdown = report_md.read_text(encoding="utf-8")

    write_json(
        dist_data / "report.json",
        {
            "ok": True,
            "result": result,
            "markdown": markdown,
            "markdownPath": "static://data/report.md",
        },
    )
    write_json(
        dist_data / "history.json",
        {
            "ok": True,
            "items": [{"name": report_md.name, "path": "static://data/report.md"}],
        },
    )
    (dist_data / "report.md").write_text(markdown, encoding="utf-8")
    print(f"Injected report {report_date} into {dist_data}")
    return 0


def parse_report_date(value: str) -> date:
    if not value:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
