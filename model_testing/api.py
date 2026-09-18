"""Local sample-test bridge for n8n. Run behind an HTTPS tunnel; not production hosting."""
import csv
import hmac
import json
import math
import os
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parent
MODEL = ROOT.parent / 'pearcedale-lgbm-model' / 'model'
LOCK = threading.Lock()
TOKEN = os.environ.get('MODEL_TEST_API_KEY', '')


def run_sample(as_of):
    run_id = uuid.uuid4().hex
    output = ROOT / 'runs' / run_id
    output.mkdir(parents=True)
    # A fresh subprocess preserves the model's expected working directory.
    # Only OUTPUT_DIR is overridden; trained models and PO rules are unchanged.
    code = (
        'import sys; from pathlib import Path; from datetime import date; '
        'import run_forecasting as model; '
        'model.OUTPUT_DIR = Path(sys.argv[1]); '
        'model.main(date.fromisoformat(sys.argv[2]))'
    )
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    env.pop('MODEL_TEST_API_KEY', None)
    with (output / 'run.log').open('w', encoding='utf-8') as log:
        completed = subprocess.run(
            [sys.executable, '-c', code, str(output), as_of.isoformat()],
            cwd=MODEL, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=90,
        )
    if completed.returncode:
        raise RuntimeError(f'Model failed. Inspect model_testing/runs/{run_id}/run.log')
    report = json.loads((output / 'evaluation_report.json').read_text())
    def read_csv(name):
        with (output / name).open(encoding='utf-8', newline='') as source:
            return list(csv.DictReader(source))
    forecasts = read_csv('hybrid_predictions_15day.csv')
    recommendations = read_csv('po_recommendations.csv')
    start, end = str(as_of + timedelta(days=1)), str(as_of + timedelta(days=15))
    ids = [row['itemCode'] for row in forecasts]
    if not ids or len(ids) != len(set(ids)) or len(ids) != report['total_skus']:
        raise RuntimeError('Empty, duplicated, or inconsistent forecast output')
    for row in forecasts:
        for field in ('baseline_forecast_15d', 'hybrid_forecast_15d'):
            row[field] = float(row[field])
            if not math.isfinite(row[field]) or row[field] < 0:
                raise RuntimeError('Invalid forecast quantity')
        if row['horizon_start'] != start or row['horizon_end'] != end:
            raise RuntimeError('Unexpected forecast horizon')
    po_ids = [row['itemCode'] for row in recommendations]
    if len(po_ids) != len(ids) or set(po_ids) != set(ids):
        raise RuntimeError('Recommendation product coverage differs from forecasts')
    for row in recommendations:
        row['recommended_order_qty'] = int(row['recommended_order_qty'])
        if row['recommended_order_qty'] < 0:
            raise RuntimeError('Negative recommendation quantity')
    # Keep server filesystem paths out of the API response.
    report.pop('outputs', None)
    return {
        'status': 'passed', 'run_id': run_id, 'test_type': 'pipeline_smoke_test',
        'inventory_is_simulated': True, 'summary': report,
        'warnings': [
            'PO quantities use simulated inventory; do not place orders from this test.',
            'This run does not measure forecast accuracy or retrain models.',
            'Existing model approximates 15-day totals from two weekly forecasts.',
            'Existing stockout dates use the computer clock, not the supplied as-of date.',
        ],
        'forecasts': forecasts, 'recommendations': recommendations,
    }


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(110)

    def respond(self, status, payload):
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/test-forecast':
            return self.respond(404, {'error': 'Not found'})
        supplied = self.headers.get('X-API-Key', '')
        if not TOKEN or not hmac.compare_digest(supplied.encode(), TOKEN.encode()):
            return self.respond(401, {'error': 'Invalid API key'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024:
                raise ValueError('Expected a small JSON request')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or set(data) != {'as_of_date'}:
                raise ValueError('Provide only as_of_date as YYYY-MM-DD')
            raw_date = data['as_of_date']
            as_of = date.fromisoformat(raw_date)
            if as_of.isoformat() != raw_date:
                raise ValueError('Use YYYY-MM-DD')
        except (ValueError, TypeError, OverflowError):
            return self.respond(400, {'error': 'Provide {"as_of_date":"YYYY-MM-DD"}'})
        if not LOCK.acquire(blocking=False):
            return self.respond(409, {'error': 'A test is running. Retry after it finishes.'})
        try:
            result = run_sample(as_of)
        except subprocess.TimeoutExpired:
            self.respond(504, {'error': 'Model exceeded the 90-second test timeout'})
        except Exception as exc:
            print(f'Test failed: {exc}', file=sys.stderr)
            self.respond(500, {'error': 'Test failed; inspect the API terminal and local run logs'})
        else:
            self.respond(200, result)
        finally:
            LOCK.release()


if __name__ == '__main__':
    if len(TOKEN) < 32:
        raise SystemExit('Set MODEL_TEST_API_KEY to a random secret of at least 32 characters.')
    port = int(os.environ.get('MODEL_TEST_PORT', '8000'))
    class LocalServer(ThreadingHTTPServer):
        allow_reuse_address = False
    print(f'Sample-test API: http://127.0.0.1:{port}/test-forecast', flush=True)
    LocalServer(('127.0.0.1', port), Handler).serve_forever()
