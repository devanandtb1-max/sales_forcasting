"""Test the deployed Modal forecast endpoint from your local machine.

Usage:
    python model_testing/modal_test.py --url <MODAL_URL> --token <TOKEN>
    python model_testing/modal_test.py --url <MODAL_URL> --token <TOKEN> --as-of 2026-09-16
"""
import argparse
import json
import sys
from datetime import date
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import ssl

def test_endpoint(url: str, token: str, as_of: str) -> dict:
    """POST to the Modal endpoint and return the parsed response."""
    payload = json.dumps({
        "as_of_date": as_of,
        "token": token,
    }).encode("utf-8")
    request = Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    context = ssl._create_unverified_context()
    with urlopen(request, timeout=120, context=context) as response:
        return json.load(response)


def validate_response(result: dict) -> list[str]:
    """Run the same 13 checks that the n8n validation node performs."""
    errors = []

    def check(ok, message):
        if not ok:
            errors.append(message)

    check(result.get("status") == "passed", f"Status is '{result.get('status')}', not 'passed'")
    check(result.get("test_type") == "pipeline_smoke_test", "Wrong test_type")

    run_id = result.get("run_id", "")
    check(isinstance(run_id, str) and len(run_id) == 32, f"Invalid run_id: {run_id!r}")

    check(result.get("inventory_is_simulated") is True, "inventory_is_simulated not True")

    forecasts = result.get("forecasts", [])
    recommendations = result.get("recommendations", [])
    summary = result.get("summary", {})

    check(len(forecasts) > 0, "No forecasts returned")
    check(len(forecasts) == summary.get("total_skus", -1), "Forecast count != total_skus")

    ids = [f.get("itemCode") for f in forecasts]
    check(len(set(ids)) == len(ids), "Duplicate forecast itemCodes")

    for row in forecasts:
        for field in ("baseline_forecast_15d", "hybrid_forecast_15d"):
            val = row.get(field)
            check(isinstance(val, (int, float)) and val >= 0, f"Invalid {field}: {val}")
        check(isinstance(row.get("itemCode"), str) and len(row["itemCode"]) > 0, "Missing itemCode")

    check(len(recommendations) == len(forecasts), "Recommendation count != forecast count")
    rec_ids = {r.get("itemCode") for r in recommendations}
    check(rec_ids == set(ids), "Recommendation coverage mismatch")
    for row in recommendations:
        qty = row.get("recommended_order_qty")
        check(isinstance(qty, int) and qty >= 0, f"Invalid order qty: {qty}")

    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Modal endpoint URL")
    parser.add_argument("--token", required=True, help="Bearer token for the endpoint")
    parser.add_argument("--as-of", default="2026-09-16", help="As-of date (YYYY-MM-DD)")
    args = parser.parse_args()

    print(f"Testing Modal endpoint: {args.url}")
    print(f"As-of date: {args.as_of}\n")

    try:
        result = test_endpoint(args.url, args.token, args.as_of)
    except HTTPError as exc:
        print(f"ERROR: HTTP {exc.code}", file=sys.stderr)
        body = exc.read().decode("utf-8", errors="replace")
        print(body[:500], file=sys.stderr)
        sys.exit(1)
    except (URLError, TimeoutError) as exc:
        print(f"ERROR: Connection failed — {exc}", file=sys.stderr)
        sys.exit(1)

    # Quick status check
    if result.get("status") == "error":
        print(f"ENDPOINT RETURNED ERROR: {result.get('error')}", file=sys.stderr)
        print(json.dumps(result, indent=2))
        sys.exit(1)

    # Full validation
    errors = validate_response(result)
    if errors:
        print("VALIDATION FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        sys.exit(1)

    summary = result.get("summary", {})
    print("=" * 50)
    print("  Modal endpoint test PASSED ✓")
    print("=" * 50)
    print(f"  Run ID:               {result['run_id']}")
    print(f"  Total SKUs:           {summary.get('total_skus')}")
    print(f"  Active promotions:    {summary.get('skus_with_active_promotion')}")
    print(f"  PO decisions:         {summary.get('po_decisions')}")
    print(f"  Total forecast qty:   {sum(f['hybrid_forecast_15d'] for f in result['forecasts']):.0f}")
    print(f"  Sample forecast:      {result['forecasts'][0]['itemCode']} → "
          f"{result['forecasts'][0]['hybrid_forecast_15d']:.1f} units")
    print()

    # Save full response for inspection
    out_path = f"model_testing/runs/modal_test_response.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"  Full response saved to: {out_path}")


if __name__ == "__main__":
    main()
