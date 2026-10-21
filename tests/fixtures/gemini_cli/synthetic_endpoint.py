"""Synthetic Gemini API endpoint for OFFLINE host probes. It is NOT a model.

It answers the pinned Gemini CLI's `streamGenerateContent` calls with a fixed,
scripted list of responses (one per call, `{WORKSPACE}` replaced by the target
path) and logs every request it receives as JSONL. It lets the real CLI run its
own tool loop against a real worktree without any real model call or key.

Usage: synthetic_endpoint.py PORT SCRIPT.json REQUEST_LOG.jsonl WORKSPACE
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    port, script_path, log_path, workspace = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    with open(script_path, encoding="utf-8") as fh:
        text = fh.read().replace("{WORKSPACE}", workspace.replace("\\", "\\\\"))
    script = json.loads(text)
    lock = threading.Lock()
    state = {"i": 0}

    def log(record):
        with lock, open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _body(self):
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                return {"_raw": raw.decode("utf-8", "replace")}

        def _json(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            log({"method": "GET", "path": self.path, "kind": "other"})
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            body = self._body()
            if ":countTokens" in self.path:
                log({"method": "POST", "path": self.path, "kind": "countTokens"})
                return self._json({"totalTokens": 10})
            if "streamGenerateContent" not in self.path:
                # Only the streamed agent turns consume the script; any other
                # model call (e.g. a routing classifier) is logged and refused.
                log({"method": "POST", "path": self.path, "kind": "non-stream-refused"})
                self.send_response(400)
                self.end_headers()
                return
            if False:
                log({"method": "POST", "path": self.path, "kind": "other"})
                return self._json({})
            with lock:
                index = state["i"]
                state["i"] += 1
            step = script[min(index, len(script) - 1)]
            log({"method": "POST", "path": self.path, "kind": "generate", "index": index, "request": body})
            payload = {"candidates": [{"content": {"role": "model", "parts": step["parts"]},
                                       "finishReason": "STOP", "index": 0}],
                       "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7,
                                         "totalTokenCount": 18},
                       "modelVersion": "synthetic-endpoint"}
            if "alt=sse" in self.path:
                data = ("data: " + json.dumps(payload) + "\r\n\r\n").encode()
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._json(payload)

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
