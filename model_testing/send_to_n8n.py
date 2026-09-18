"""Run the sample model locally, then POST its results to your n8n test webhook."""
import argparse
from datetime import date
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from api import ROOT, run_sample

ALLOWED_HOST = 'n8n.srv1691210.hstgr.cloud'
DEFAULT_TEST_URL = f'https://{ALLOWED_HOST}/webhook-test/pearcedale-model-results-test'
DEFAULT_KEY_FILE = ROOT / 'runs' / 'webhook-key.txt'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the key or results to another endpoint.


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != ALLOWED_HOST
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.query or parsed.fragment
            or not parsed.path.startswith('/webhook-test/')):
        raise ValueError(f'Use the HTTPS Test URL on {ALLOWED_HOST} (path /webhook-test/...).')
    return url


def send_payload(url, key, payload):
    validate_url(url)
    if not key:
        raise ValueError('A webhook Header Auth key is required.')
    request = Request(url, data=json.dumps(payload, allow_nan=False).encode('utf-8'),
                      headers={'Content-Type': 'application/json', 'X-API-Key': key}, method='POST')
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
        result = json.load(response)
    if result.get('status') != 'passed' or result.get('run_id') != payload['run_id']:
        raise RuntimeError('n8n did not confirm successful validation of this run.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', type=date.fromisoformat, default=date(2026, 9, 16))
    parser.add_argument('--payload', type=Path, help='Resend an existing payload.json without rerunning the model')
    parser.add_argument('--url', default=DEFAULT_TEST_URL, help='n8n webhook Test URL')
    parser.add_argument('--key-file', type=Path, default=DEFAULT_KEY_FILE,
                        help='Local file containing the webhook key')
    args = parser.parse_args()
    url = validate_url(args.url)
    if not args.key_file.is_file():
        raise ValueError(f'Webhook key file missing: {args.key_file}')
    key = args.key_file.read_text(encoding='utf-8-sig').strip()
    if not key:
        raise ValueError(f'Webhook key file is empty: {args.key_file}')
    if args.payload:
        payload_path = args.payload.resolve()
        payload = json.loads(payload_path.read_text(encoding='utf-8-sig'))
    else:
        print('Running the local model. Do not start the n8n listener yet.', flush=True)
        payload = run_sample(args.as_of)
        payload_path = ROOT / 'runs' / payload['run_id'] / 'payload.json'
        payload_path.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
    print(f"Prepared {len(payload['forecasts'])} forecasts. Saved: {payload_path}")
    print('This is an execution test. Inventory is simulated; no real POs are submitted.')
    input('In n8n click Execute workflow to listen, then press Enter here to send: ')
    try:
        result = send_payload(url, key, payload)
    except HTTPError as exc:
        hints = {401: 'Check your Header Auth key.', 403: 'Check authentication or server access rules.',
                 404: 'Click Execute workflow again and use the current Test URL.',
                 422: 'The workflow rejected the payload; inspect its validation output.'}
        raise RuntimeError(f'HTTP {exc.code}. {hints.get(exc.code, "Inspect the n8n execution.")} '
                           f'Payload retained at {payload_path}; do not rerun the model to retry.') from None
    except (URLError, TimeoutError):
        raise RuntimeError(f'Connection failed or timed out. Check n8n before retrying; '
                           f'it may have received the request. Payload retained at {payload_path}.') from None
    (payload_path.parent / 'n8n_response.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f"n8n validation PASSED: {result['total_skus']} products, run {result['run_id']}")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
