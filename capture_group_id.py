"""Temporary script to capture group_id from Seatalk Bot Added event."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        event_type = body.get("event_type", "")

        if event_type == "event_verification":
            challenge = body["event"]["seatalk_challenge"]
            response = json.dumps({"seatalk_challenge": challenge}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response)
            print("✅ Verification challenge passed")
        else:
            self.send_response(200)
            self.end_headers()
            print(f"\n📨 Event received: {event_type}")
            print(json.dumps(body, indent=2))
            group_id = (body.get("event") or {}).get("group_id")
            if group_id:
                print(f"\n🎯 GROUP ID: {group_id}\n")

    def log_message(self, *args):
        pass  # suppress request logs


if __name__ == "__main__":
    print("Listening on http://localhost:8081 ...")
    HTTPServer(("", 8081), Handler).serve_forever()
