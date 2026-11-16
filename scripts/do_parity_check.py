"""Compare GET responses between two api-ncac servers (Render vs DigitalOcean).

    python3 scripts/do_parity_check.py \
        --a https://api-ncac.onrender.com \
        --b https://seahorse-app-o3p3o.ondigitalocean.app/ncac \
        [--token <bearer>] [--only "Finance"] [--list]

Reads the route list from the running server's /openapi.json (server A).
Only GET endpoints without path params and without required query params run.
Endpoints that scrape ATMS live, run pipelines, or scan big tables are skipped
(SKIP_TAGS / SKIP_PATHS) — those go on the manual list instead.
One request at a time with a pause between, so the shared Postgres
(25 connections) and Mongo never see a burst.

Standard library only.
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

SKIP_TAGS = {
    "ATMS Withdraw", "ATMS Open Job", "ATMS Maintenance Request", "ATMS TMS",
    "ATMS SKU Lookup", "Pipeline", "Driving Distance", "Mixer Compensation",
    "Incident Analytics", "System Status",
}
SKIP_PATHS = {"/", "/docs", "/redoc", "/openapi.json", "/health"}


def fetch(url, token, timeout):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        body = e.read()
        status = e.code
    except Exception as e:  # timeout / connection error
        return None, str(e).encode(), time.time() - t0
    return status, body, time.time() - t0


def norm(body):
    try:
        return json.dumps(json.loads(body), sort_keys=True, ensure_ascii=False).encode()
    except Exception:
        return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--token")
    ap.add_argument("--only", help="substring of tag to run")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--list", action="store_true", help="print what would run and exit")
    args = ap.parse_args()
    a, b = args.a.rstrip("/"), args.b.rstrip("/")

    _, spec, _ = fetch(a + "/openapi.json", None, 60)
    paths = json.loads(spec)["paths"]
    run, skipped = [], []
    for path, ops in sorted(paths.items()):
        op = ops.get("get")
        if not op:
            continue
        tag = (op.get("tags") or [""])[0]
        required_q = [p["name"] for p in op.get("parameters", []) if p.get("in") == "query" and p.get("required")]
        reason = None
        if path in SKIP_PATHS:
            reason = "meta"
        elif "{" in path:
            reason = "path param"
        elif tag in SKIP_TAGS:
            reason = f"heavy/live ({tag})"
        elif required_q:
            reason = "required query: " + ",".join(required_q)
        elif args.only and args.only.lower() not in tag.lower():
            reason = "filtered"
        (skipped if reason else run).append((path, tag, reason))

    if args.list:
        for p, t, _ in run:
            print(f"RUN   {t:28} {p}")
        for p, t, r in skipped:
            print(f"SKIP  {t:28} {p}  ({r})")
        print(f"\n{len(run)} to run, {len(skipped)} skipped")
        return

    ok = 0
    for path, tag, _ in run:
        sa, ba, ta = fetch(a + path, args.token, args.timeout)
        time.sleep(args.pause)
        sb, bb, tb = fetch(b + path, args.token, args.timeout)
        time.sleep(args.pause)
        same = sa == sb and hashlib.sha1(norm(ba)).digest() == hashlib.sha1(norm(bb)).digest()
        ok += same
        print(f"{'PASS' if same else 'DIFF'} {sa}/{sb} {path}  ({ta*1000:.0f}ms / {tb*1000:.0f}ms, {len(ba)}B/{len(bb)}B)")
        sys.stdout.flush()
    print(f"\n{ok}/{len(run)} GET routes match · {len(skipped)} skipped (run with --list to see why)")


if __name__ == "__main__":
    main()
