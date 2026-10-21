"""Synthetic Gemini API endpoint for offline host probing. NOT a model.
Replays a scripted list of responses (one per generateContent call) and logs
every request verbatim. Usage: fake_gemini.py PORT SCRIPT.json LOG.jsonl"""
import json, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
port, script_path, log_path = int(sys.argv[1]), sys.argv[2], sys.argv[3]
script = json.load(open(script_path, encoding="utf-8"))
lock = threading.Lock(); state = {"i": 0}
def log(rec):
    with lock, open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b""
        try: return json.loads(raw or b"{}")
        except Exception: return {"_raw": raw.decode("utf-8", "replace")}
    def do_GET(self):
        log({"method": "GET", "path": self.path}); self.send_response(404); self.end_headers()
    def do_POST(self):
        body = self._body(); path = self.path
        if ":countTokens" in path:
            log({"method": "POST", "path": path, "kind": "countTokens"})
            return self._json({"totalTokens": 10})
        if "GenerateContent" in path or "generateContent" in path:
            with lock:
                i = state["i"]; state["i"] += 1
            resp = script[min(i, len(script) - 1)]
            log({"method": "POST", "path": path, "kind": "generate", "index": i, "request": body})
            payload = {"candidates": [{"content": {"role": "model", "parts": resp["parts"]},
                                       "finishReason": "STOP", "index": 0}],
                       "usageMetadata": resp.get("usage", {"promptTokenCount": 11, "candidatesTokenCount": 7, "totalTokenCount": 18}),
                       "modelVersion": resp.get("modelVersion", "synthetic-model")}
            if "alt=sse" in path:
                data = ("data: " + json.dumps(payload) + "\r\n\r\n").encode()
                self.send_response(200); self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data); return
            return self._json(payload)
        log({"method": "POST", "path": path, "kind": "other", "request": body})
        self._json({})
    def _json(self, obj):
        data = json.dumps(obj).encode(); self.send_response(200)
        self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
