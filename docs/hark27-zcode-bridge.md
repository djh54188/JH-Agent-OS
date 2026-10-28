# Hark-27 ZCode thin bridge (offline candidate)

Status: candidate pending independent review. Real model calls: 0. Not wired into the GUI.

## What it is
`src/agent_session_zcode.py` - `ZCodeAgentSessionAdapter`, a real `AgentSessionAdapter`
subclass. The tool loop is ZCode's own (`zcode.cjs --prompt ... --output-format stream-json`).
The bridge binds one run, persists it before launch, launches the host with an isolated home and
an explicit provider/model, and reads back the host's own NDJSON. It is not a ModelAdapter, does
not translate protocols and never rewrites ZCode events into Gemini CLI events.

Supported: capabilities, submit, status, collect. continue and cancel raise
UnsupportedCapability. Scope: read and search only (Read, Grep, Glob).

## Launch shape (from the official source, unverified on the installed build)
```
<node> <zcode.cjs> --prompt <instructions> --cwd <workspace> --mode plan
  --disallowed-tools "<every non read/search tool name known from the contracts>"
  --workflow-mode disabled --output-format stream-json --no-color
env: PATH/SYSTEMROOT/WINDIR/COMSPEC/PATHEXT/TEMP/TMP only, plus HOME=USERPROFILE=
     ZCODE_DATA_BASE_DIR=<run home>, APPDATA/LOCALAPPDATA under the run home,
     ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=<run home>/.zcode/v2/provider_config.json
```
There is no `--model` flag; the model is bound only through the isolated provider file's
`defaultModelSelection`, and every `model_request` event must name exactly that provider/model.
A fallback event, another model, or no observed `model_request` is non-pass.

## Pass / non-pass rules (mechanical only; semantic review stays separate)
- API / launch: `not-launched:<stage>` or launch failure -> failed, never retried on that root.
- Host declaration: exit code + result line + `projection.status` -> `host_completed` or not.
- Mechanical: every event's sessionId/traceId equals the result's; every tool result belongs to a
  call requested in the same turn; only Read/Grep/Glob; no `readOnly: false`; every path inside
  the workspace (realpath); any `permission.requested` or non-deny resolution fails.
- Unknown: unparseable lines, missing/duplicate result line, unobserved input/result, unobserved
  model -> `unknown`, never pass. Absent usage is recorded as unknown, not 0.
- `plan` is not a sandbox: out-of-workspace reads are detected afterwards, not prevented.

## Registry
`<evidence root>/registry.jsonl` (append-only, fsync) with `reserved` written before anything
else, then `dispatched` / `dispatch_failed`, `exited`, `collected`. Same key + same request
returns the same run with no relaunch; same key + different request raises IdempotencyConflict;
any other key or attempt on a root that already holds a run is refused. Reserved-without-outcome
or dispatched-without-exit after reopen is `unknown` and frozen. Corrupt, partial, unknown-shape,
locked, or traces-without-registry roots are refused (never treated as fresh).

## Reusing the user's existing ZCode provider (TEMPLATE FOR REVIEW ONLY)
Not executed in Hark-27: the bridge does not read the user's `~/.zcode` files or key and does not
change the user's default provider or model. A later, separately authorized step could:
1. Take the provider id, Anthropic base URL and model id the user confirms by hand (for the
   existing proxy: API type `anthropic-messages`, base URL `http://localhost:8787`, model alias
   `gemini-3.8-flash-high`; the alias is the proxy's name, not a verified official model).
2. Put the key in an environment variable chosen for that run and pass its NAME as `key_env`.
3. The bridge then writes ONLY into the isolated run home:
```json
{"schemaVersion": 1, "config": {
  "providerConfigRules": {"providerRules": [{"providerId": "<provider id>", "providerName": "<provider id>",
    "config": {"group": "standard-personal", "access": {"type": "api-key", "apiKey": "<from key_env at dispatch>"},
               "api": {"type": "anthropic-messages", "baseUrl": "<base URL>"},
               "personalModelIds": ["<model id>"]}}]},
  "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
  "defaultModelSelection": {"providerId": "<provider id>", "modelId": "<model id>"}}}
```
The key file lives outside the evidence root and is never archived. Real use still needs: review
of this candidate, a zero-call host probe that can be proven isolated, user confirmation of the
account, quota, cost bound and run count, and authorization to contact localhost:8787.

## Windows entry
`scripts/win/hark27-zcode-prepare.ps1` (Prepare / offline replay only) and its driver
`scripts/win/hark27_zcode_replay.py`. It verifies the commit, reviewed trees, driver blob and the
on-disk bytes before running repo code, optionally hashes zcode.cjs (never executes it), and
replays the real bridge against the fake host. Exit codes 0/2/3/4/5/1 as documented in the script.
