#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""baseline-drift-ledger — append-only ledger for benchmark baselines.

Every entry is hash-chained (sha256 over prev hash + canonical JSON), so any
retro-edit is detectable. Environment annotations on each entry enable drift
attribution: when a score moves beyond threshold, the tool diffs the two
entries' env fields and lists the changes as candidate causes.

Ledger format: JSONL, one entry per line:
  {"ts": "...", "suite": "...", "metric": "...", "value": 1.23,
   "env": {"k": "v"}, "note": "...", "hash": "..."}

Subcommands:
  add     append an entry (validates schema + monotonic ts, computes hash)
  check   compare latest vs previous value of one suite/metric; attribute drift
  verify  recompute the whole hash chain
  report  per suite+metric summary (count, first, last, latest drift)

Exit codes: 0 clean, 1 findings (drift / broken chain), 2 I/O or usage error.
Pure stdlib. Python 3.8+.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime

__version__ = "0.1.1"

GENESIS = "GENESIS"


def canonical_entry(entry):
    body = {k: v for k, v in entry.items() if k != "hash"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False)


def entry_hash(prev_hash, entry):
    return hashlib.sha256(
        (prev_hash + "\n" + canonical_entry(entry)).encode("utf-8")).hexdigest()


def load_ledger(text):
    """Parse JSONL; return (entries, errors)."""
    entries, errors = [], []
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError("not an object")
            entries.append(obj)
        except ValueError as e:
            errors.append({"line": ln, "message": str(e)})
    return entries, errors


def validate_entry(entry):
    """Return list of schema problems (empty = valid)."""
    problems = []
    for field in ("ts", "suite", "metric", "value", "env"):
        if field not in entry:
            problems.append("missing field %r" % field)
    if "ts" in entry:
        try:
            datetime.fromisoformat(entry["ts"])
        except (ValueError, TypeError):
            problems.append("ts is not ISO 8601: %r" % entry.get("ts"))
    if "value" in entry and not isinstance(entry["value"], (int, float)) \
            or isinstance(entry.get("value"), bool):
        problems.append("value must be a number")
    if "env" in entry and not isinstance(entry.get("env"), dict):
        problems.append("env must be an object")
    return problems


def cmd_add(args):
    try:
        with open(args.file, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    except OSError as e:
        print("error: cannot read %s: %s" % (args.file, e), file=sys.stderr)
        return 2
    entries, errors = load_ledger(text)
    if errors:
        print("error: ledger has malformed lines: %s" % errors[:3],
              file=sys.stderr)
        return 2
    env = {}
    for pair in (args.env or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            print("error: --env pair %r is not k=v" % pair, file=sys.stderr)
            return 2
        k, v = pair.split("=", 1)
        env[k.strip()] = v.strip()
    entry = {"ts": args.ts, "suite": args.suite, "metric": args.metric,
             "value": args.value, "env": env}
    if args.note:
        entry["note"] = args.note
    problems = validate_entry(entry)
    if problems:
        print("error: invalid entry: %s" % "; ".join(problems), file=sys.stderr)
        return 2
    if entries:
        last_ts = entries[-1].get("ts", "")
        if str(args.ts) < str(last_ts):
            print("error: append-only violation: ts %s precedes last entry %s"
                  % (args.ts, last_ts), file=sys.stderr)
            return 2
    prev_hash = entries[-1]["hash"] if entries else GENESIS
    entry["hash"] = entry_hash(prev_hash, entry)
    with open(args.file, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    print("added: suite=%s metric=%s value=%s ts=%s"
          % (entry["suite"], entry["metric"], entry["value"], entry["ts"]))
    return 0


def _series(entries, suite, metric):
    return [e for e in entries
            if e.get("suite") == suite and e.get("metric") == metric]


def _env_diff(old, new):
    old, new = old or {}, new or {}
    changed = ["%s: %s -> %s" % (k, old[k], new[k])
               for k in sorted(set(old) & set(new)) if old[k] != new[k]]
    added = ["%s: + %s" % (k, new[k]) for k in sorted(set(new) - set(old))]
    removed = ["%s: - %s" % (k, old[k]) for k in sorted(set(old) - set(new))]
    return changed + added + removed


def cmd_check(args):
    try:
        with open(args.file, "r", encoding="utf-8-sig") as f:
            entries, errors = load_ledger(f.read())
    except OSError as e:
        print("error: cannot read %s: %s" % (args.file, e), file=sys.stderr)
        return 2
    if errors:
        print("error: malformed lines: %s" % errors[:3], file=sys.stderr)
        return 2
    series = _series(entries, args.suite, args.metric)
    if len(series) < 2:
        print("no drift check possible: %d entr(ies) for %s/%s"
              % (len(series), args.suite, args.metric))
        return 0
    old, new = series[-2], series[-1]
    ov, nv = old["value"], new["value"]
    findings = []
    if ov == 0:
        if nv != 0:
            findings.append({"rule": "baseline-zero", "severity": "error",
                             "message": "previous baseline is 0; new value "
                                        "%s is non-comparable" % nv})
        drift = 0.0
    else:
        drift = (nv - ov) / abs(ov)
        if abs(drift) > args.threshold:
            findings.append({"rule": "drift-beyond-threshold",
                             "severity": "error",
                             "message": "%s/%s: %s -> %s (%+.1f%%) "
                                        "threshold %.1f%%"
                                        % (args.suite, args.metric, ov, nv,
                                           drift * 100, args.threshold * 100)})
    causes = _env_diff(old.get("env"), new.get("env"))
    report = {"suite": args.suite, "metric": args.metric,
              "old": ov, "new": nv, "drift": round(drift, 6),
              "threshold": args.threshold,
              "findings": findings,
              "env_changes": causes}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for f in findings:
            print("%-24s %s" % (f["rule"], f["message"]))
        if causes:
            print("env attribution (candidate causes):")
            for c in causes:
                print("  " + c)
        elif findings:
            print("env attribution: no env field changes found — "
                  "drift is unexplained")
        print("checked %s/%s: drift %+.2f%%, %d finding(s), %d env change(s)"
              % (args.suite, args.metric, drift * 100, len(findings),
                 len(causes)))
    return 1 if findings else 0


def cmd_verify(args):
    try:
        with open(args.file, "r", encoding="utf-8-sig") as f:
            entries, errors = load_ledger(f.read())
    except OSError as e:
        print("error: cannot read %s: %s" % (args.file, e), file=sys.stderr)
        return 2
    findings = []
    if errors:
        for e in errors:
            print("error: malformed line %d: %s" % (e["line"], e["message"]),
                  file=sys.stderr)
        return 2
    prev = GENESIS
    for i, e in enumerate(entries, 1):
        problems = validate_entry(e)
        if problems:
            findings.append({"rule": "schema-invalid", "severity": "error",
                             "message": "entry %d: %s" % (i, "; ".join(problems))})
        want = entry_hash(prev, e)
        if e.get("hash") != want:
            findings.append({"rule": "chain-broken", "severity": "error",
                             "message": "entry %d hash mismatch "
                                        "(expected %s, found %s)"
                                        % (i, want[:12], str(e.get("hash"))[:12])})
        prev = e.get("hash", want)
    if args.json:
        print(json.dumps({"entries": len(entries), "findings": findings},
                         ensure_ascii=False, indent=2))
    else:
        for f in findings:
            print("%-14s %s" % (f["rule"], f["message"]))
        print("verified %d entries: %d finding(s)" % (len(entries), len(findings)))
    return 1 if findings else 0


def cmd_report(args):
    try:
        with open(args.file, "r", encoding="utf-8-sig") as f:
            entries, errors = load_ledger(f.read())
    except OSError as e:
        print("error: cannot read %s: %s" % (args.file, e), file=sys.stderr)
        return 2
    groups = {}
    for e in entries:
        groups.setdefault((e.get("suite"), e.get("metric")), []).append(e)
    lines = []
    for (suite, metric), series in sorted(groups.items()):
        vals = [e["value"] for e in series]
        lines.append({"suite": suite, "metric": metric, "count": len(series),
                      "first": vals[0], "last": vals[-1]})
    if args.json:
        print(json.dumps({"series": lines, "parse_errors": len(errors)},
                         ensure_ascii=False, indent=2))
    else:
        for l in lines:
            print("%s/%s: %d entr(ies), %s -> %s"
                  % (l["suite"], l["metric"], l["count"], l["first"], l["last"]))
        print("reported %d series: %d parse error(s)" % (len(lines), len(errors)))
    return 1 if errors else 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="baseline-drift-ledger",
        description="Append-only, hash-chained ledger for benchmark baselines "
                    "with environment-drift attribution.")
    p.add_argument("--version", action="version",
                   version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="append one entry")
    pa.add_argument("file")
    pa.add_argument("--suite", required=True)
    pa.add_argument("--metric", required=True)
    pa.add_argument("--value", required=True, type=float)
    pa.add_argument("--ts", required=True, help="ISO 8601 timestamp")
    pa.add_argument("--env", default="", help="k=v,k=v environment annotation")
    pa.add_argument("--note", default="")

    pc = sub.add_parser("check", help="drift check for one series")
    pc.add_argument("file")
    pc.add_argument("--suite", required=True)
    pc.add_argument("--metric", required=True)
    pc.add_argument("--threshold", type=float, default=0.05)
    pc.add_argument("--json", action="store_true")

    pv = sub.add_parser("verify", help="recompute hash chain")
    pv.add_argument("file")
    pv.add_argument("--json", action="store_true")

    pr = sub.add_parser("report", help="per-series summary")
    pr.add_argument("file")
    pr.add_argument("--json", action="store_true")

    args = p.parse_args(argv)
    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "verify":
        return cmd_verify(args)
    return cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
