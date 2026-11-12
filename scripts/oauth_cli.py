"""OAuth CLI for Personal Brain — Gmail & Calendar authorization flow.

Usage:
    python scripts/oauth_cli.py --credentials /path/to/client_secret.json \
        --scopes gmail,calendar \
        --output /path/to/google_token.json

Runs a local OAuth 2.0 flow and saves credentials to a JSON file that can
be used by brain_sync_daemon.py for syncing Gmail/Calendar.
"""

import argparse
import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.oauth2.credentials import Credentials
except ImportError:
    print("google-auth-oauthlib not installed. Run: pip install google-auth-oauthlib", file=sys.stderr)
    sys.exit(1)

SCOPES = {
    "gmail": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ],
    "calendar": [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    ],
}


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handle OAuth redirect from localhost."""

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            query = parse_qs(parsed.query)
            if "code" in query:
                self.server.auth_code = query["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<h1>OAuth success!</h1><p>You can close this window.</p>"
                )
            elif "error" in query:
                self.server.auth_error = query["error"][0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(f"<h1>OAuth error: {query['error'][0]}</h1>".encode())
            else:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # silence default logging


def main():
    parser = argparse.ArgumentParser(description="OAuth CLI for Personal Brain")
    parser.add_argument(
        "--credentials", "-c",
        required=True,
        help="Path to Google OAuth client_secret.json",
    )
    parser.add_argument(
        "--scopes", "-s",
        default="gmail,calendar",
        help="Comma-separated scopes: gmail,calendar (default: gmail,calendar)",
    )
    parser.add_argument(
        "--output", "-o",
        default="google_token.json",
        help="Output token file path (default: google_token.json)",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8080,
        help="Local port for OAuth callback (default: 8080)",
    )
    args = parser.parse_args()

    requested = [s.strip() for s in args.scopes.split(",") if s.strip()]
    scopes = []
    for name in requested:
        if name in SCOPES:
            scopes.extend(SCOPES[name])
        else:
            print(f"Unknown scope: {name} (available: {', '.join(SCOPES)})", file=sys.stderr)
            return 1
    scopes = list(dict.fromkeys(scopes))  # dedupe preserving order

    flow = InstalledAppFlow.from_client_secrets_file(args.credentials, scopes=scopes)

    # Run local server flow
    creds = flow.run_local_server(
        port=args.port,
        success_message="OAuth success! You can close this window.",
        open_browser=True,
    )

    output_path = Path(args.output)
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }
    output_path.write_text(json.dumps(token_data, indent=2))
    print(f"OAuth token saved to: {output_path}")
    print("Add GOOGLE_CREDENTIALS_PATH or set env vars for brain_sync_daemon.py to use this.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
