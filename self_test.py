# -*- coding: utf-8 -*-
"""self_test for baseline-drift-ledger — v2 template, four defenses:
1) SystemExit/Exception capture  2) Unicode-safe matching
3) fixtures written with newline=""  4) last line: SUMMARY: PASS n/n
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_ledger as mod  # noqa: E402

main = mod.main


def run_cli(argv):
    buf_out, buf_err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            code = main(argv) or 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception as e:  # noqa: BLE001
        code = 1
        buf_err.write(repr(e))
    return code, buf_out.getvalue(), buf_err.getvalue()


FIX = tempfile.mkdtemp(prefix="bdl_selftest_")


def wfile(name, content):
    path = os.path.join(FIX, name)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return path


def entry(ts, suite, metric, value, env=None, note=None):
    e = {"ts": ts, "suite": suite, "metric": metric, "value": value,
         "env": env or {}}
    if note:
        e["note"] = note
    return e


def build_ledger(name, *entries):
    """Write a valid hash-chained ledger and return its path."""
    lines = []
    prev = mod.GENESIS
    for e in entries:
        e = dict(e)
        e["hash"] = mod.entry_hash(prev, e)
        prev = e["hash"]
        lines.append(json.dumps(e, ensure_ascii=False, sort_keys=True))
    return wfile(name, "\n".join(lines) + ("\n" if lines else ""))


CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ---------- [A] functional: hash chain ----------
@case("entry_hash deterministic")
def t01():
    e = entry("2026-01-01", "s", "m", 1.0)
    assert mod.entry_hash(mod.GENESIS, e) == mod.entry_hash(mod.GENESIS, e)


@case("entry_hash chains on prev hash")
def t02():
    e = entry("2026-01-01", "s", "m", 1.0)
    assert mod.entry_hash("A", e) != mod.entry_hash("B", e)


@case("canonical entry excludes hash field")
def t03():
    e = entry("2026-01-01", "s", "m", 1.0)
    e["hash"] = "x"
    assert "hash" not in mod.canonical_entry(e)


@case("verify clean two-entry ledger passes")
def t04():
    path = build_ledger("v1.jsonl",
                        entry("2026-01-01", "s", "m", 1.0),
                        entry("2026-01-02", "s", "m", 1.01))
    code, out, _ = run_cli(["verify", path])
    assert code == 0 and "verified 2 entries: 0 finding(s)" in out, out


@case("verify single-entry ledger passes")
def t05():
    path = build_ledger("v2.jsonl", entry("2026-01-01", "s", "m", 1.0))
    code, out, _ = run_cli(["verify", path])
    assert code == 0, out


@case("verify empty ledger passes")
def t06():
    path = wfile("v3.jsonl", "")
    code, out, _ = run_cli(["verify", path])
    assert code == 0 and "verified 0 entries" in out, out


@case("tampered value detected")
def t07():
    path = build_ledger("v4.jsonl", entry("2026-01-01", "s", "m", 1.0),
                        entry("2026-01-02", "s", "m", 1.01))
    text = open(path, encoding="utf-8").read().replace("1.01", "9.99")
    path2 = wfile("v4b.jsonl", text)
    code, out, _ = run_cli(["verify", path2])
    assert code == 1 and "chain-broken" in out, out


@case("deleted middle entry breaks chain")
def t08():
    path = build_ledger("v5.jsonl",
                        entry("2026-01-01", "s", "m", 1.0),
                        entry("2026-01-02", "s", "m", 2.0),
                        entry("2026-01-03", "s", "m", 3.0))
    lines = [l for l in open(path, encoding="utf-8").read().splitlines()
             if '"value": 2.0' not in l]
    path2 = wfile("v5b.jsonl", "\n".join(lines) + "\n")
    code, out, _ = run_cli(["verify", path2])
    assert code == 1 and "chain-broken" in out, out


# ---------- [A] functional: add ----------
@case("add creates ledger from nothing")
def t09():
    path = os.path.join(FIX, "a1.jsonl")
    code, out, _ = run_cli(["add", path, "--suite", "s", "--metric", "m",
                            "--value", "1.5", "--ts", "2026-01-01T00:00:00",
                            "--env", "os=win"])
    assert code == 0 and "added" in out, out
    data = [json.loads(l) for l in open(path, encoding="utf-8")]
    assert len(data) == 1 and data[0]["env"] == {"os": "win"}
    assert data[0]["hash"] == mod.entry_hash(mod.GENESIS, {
        k: v for k, v in data[0].items() if k != "hash"})


@case("add appends with correct chain hash")
def t10():
    path = os.path.join(FIX, "a2.jsonl")
    run_cli(["add", path, "--suite", "s", "--metric", "m", "--value", "1",
             "--ts", "2026-01-01T00:00:00"])
    code, _, _ = run_cli(["add", path, "--suite", "s", "--metric", "m",
                          "--value", "2", "--ts", "2026-01-02T00:00:00"])
    assert code == 0
    code, out, _ = run_cli(["verify", path])
    assert code == 0, out


@case("add rejects ts going backwards")
def t11():
    path = os.path.join(FIX, "a3.jsonl")
    run_cli(["add", path, "--suite", "s", "--metric", "m", "--value", "1",
             "--ts", "2026-01-05T00:00:00"])
    code, _, err = run_cli(["add", path, "--suite", "s", "--metric", "m",
                            "--value", "1", "--ts", "2026-01-01T00:00:00"])
    assert code == 2 and "append-only" in err, err


@case("add rejects non-numeric value")
def t12():
    path = os.path.join(FIX, "a4.jsonl")
    code, _, err = run_cli(["add", path, "--suite", "s", "--metric", "m",
                            "--value", "abc", "--ts", "2026-01-01"])
    assert code == 2, err


@case("add rejects bad ts format")
def t13():
    path = os.path.join(FIX, "a5.jsonl")
    code, _, err = run_cli(["add", path, "--suite", "s", "--metric", "m",
                            "--value", "1", "--ts", "not-a-date"])
    assert code == 2 and "ISO 8601" in err, err


@case("add rejects malformed env pair")
def t14():
    path = os.path.join(FIX, "a6.jsonl")
    code, _, err = run_cli(["add", path, "--suite", "s", "--metric", "m",
                            "--value", "1", "--ts", "2026-01-01",
                            "--env", "noequalsign"])
    assert code == 2 and "k=v" in err, err


@case("add with note stored")
def t15():
    path = os.path.join(FIX, "a7.jsonl")
    run_cli(["add", path, "--suite", "s", "--metric", "m", "--value", "1",
             "--ts", "2026-01-01", "--note", "first run"])
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["note"] == "first run"


# ---------- [A] functional: check & attribution ----------
@case("drift within threshold passes")
def t16():
    path = build_ledger("c1.jsonl",
                        entry("2026-01-01", "s", "m", 1.0),
                        entry("2026-01-02", "s", "m", 1.04))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert code == 0 and "0 finding(s)" in out, out


@case("drift beyond threshold fails with percent")
def t17():
    path = build_ledger("c2.jsonl",
                        entry("2026-01-01", "s", "m", 1.0),
                        entry("2026-01-02", "s", "m", 1.20))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert code == 1 and "drift-beyond-threshold" in out and "+20.0%" in out, out


@case("negative drift detected")
def t18():
    path = build_ledger("c3.jsonl",
                        entry("2026-01-01", "s", "m", 100.0),
                        entry("2026-01-02", "s", "m", 80.0))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert code == 1 and "-20.0%" in out, out


@case("custom threshold respected")
def t19():
    path = build_ledger("c4.jsonl",
                        entry("2026-01-01", "s", "m", 1.0),
                        entry("2026-01-02", "s", "m", 1.03))
    code, _, _ = run_cli(["check", path, "--suite", "s", "--metric", "m",
                          "--threshold", "0.01"])
    assert code == 1


@case("env attribution lists changed keys")
def t20():
    path = build_ledger("c5.jsonl",
                        entry("2026-01-01", "s", "m", 1.0,
                              env={"os": "win", "py": "3.11"}),
                        entry("2026-01-02", "s", "m", 1.30,
                              env={"os": "win", "py": "3.12"}))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert code == 1 and "py: 3.11 -> 3.12" in out, out


@case("env attribution lists added and removed keys")
def t21():
    path = build_ledger("c6.jsonl",
                        entry("2026-01-01", "s", "m", 1.0, env={"gpu": "on"}),
                        entry("2026-01-02", "s", "m", 1.30, env={"cpu": "new"}))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert "gpu: - on" in out and "cpu: + new" in out, out


@case("unexplained drift reported when env identical")
def t22():
    path = build_ledger("c7.jsonl",
                        entry("2026-01-01", "s", "m", 1.0, env={"os": "x"}),
                        entry("2026-01-02", "s", "m", 1.50, env={"os": "x"}))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert "unexplained" in out, out


@case("check with single entry is a no-op")
def t23():
    path = build_ledger("c8.jsonl", entry("2026-01-01", "s", "m", 1.0))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert code == 0 and "no drift check possible" in out, out


@case("check unknown series is a no-op")
def t24():
    path = build_ledger("c9.jsonl", entry("2026-01-01", "a", "b", 1.0))
    code, out, _ = run_cli(["check", path, "--suite", "zz", "--metric", "yy"])
    assert code == 0


@case("baseline-zero flagged when old is 0")
def t25():
    path = build_ledger("c10.jsonl",
                        entry("2026-01-01", "s", "m", 0.0),
                        entry("2026-01-02", "s", "m", 5.0))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert code == 1 and "baseline-zero" in out, out


@case("zero-to-zero is clean")
def t26():
    path = build_ledger("c11.jsonl",
                        entry("2026-01-01", "s", "m", 0.0),
                        entry("2026-01-02", "s", "m", 0.0))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert code == 0, out


@case("series isolation: only same suite+metric compared")
def t27():
    path = build_ledger("c12.jsonl",
                        entry("2026-01-01", "s", "m", 1.0),
                        entry("2026-01-02", "s", "other", 999.0),
                        entry("2026-01-03", "s", "m", 1.01))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert code == 0, out


@case("check --json parseable with fields")
def t28():
    path = build_ledger("c13.jsonl",
                        entry("2026-01-01", "s", "m", 1.0),
                        entry("2026-01-02", "s", "m", 1.5))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m",
                            "--json"])
    data = json.loads(out)
    assert code == 1 and data["drift"] == 0.5 and data["old"] == 1.0


@case("check uses last two entries of series")
def t29():
    path = build_ledger("c14.jsonl",
                        entry("2026-01-01", "s", "m", 1.0),
                        entry("2026-01-02", "s", "m", 5.0),
                        entry("2026-01-03", "s", "m", 5.1))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert code == 0 and "drift +2.00%" in out, out


# ---------- [A] functional: report ----------
@case("report lists all series")
def t30():
    path = build_ledger("r1.jsonl",
                        entry("2026-01-01", "s1", "m", 1.0),
                        entry("2026-01-02", "s1", "m", 2.0),
                        entry("2026-01-03", "s2", "x", 5.0))
    code, out, _ = run_cli(["report", path])
    assert code == 0 and "s1/m: 2 entr(ies), 1.0 -> 2.0" in out \
        and "s2/x: 1 entr(ies)" in out, out


@case("report --json parseable")
def t31():
    path = build_ledger("r2.jsonl", entry("2026-01-01", "s", "m", 1.0))
    code, out, _ = run_cli(["report", path, "--json"])
    data = json.loads(out)
    assert code == 0 and data["series"][0]["count"] == 1


@case("report empty ledger")
def t32():
    path = wfile("r3.jsonl", "")
    code, out, _ = run_cli(["report", path])
    assert code == 0 and "reported 0 series" in out, out


@case("unicode suite names round-trip")
def t33():
    path = os.path.join(FIX, "r4.jsonl")
    code, _, _ = run_cli(["add", path, "--suite", "推理", "--metric", "准确率",
                          "--value", "0.9", "--ts", "2026-01-01"])
    assert code == 0
    code, out, _ = run_cli(["report", path])
    assert code == 0 and "推理/准确率" in out, out


# ---------- [B] error paths ----------
@case("verify missing file exits 2")
def t34():
    code, _, err = run_cli(["verify", os.path.join(FIX, "nope.jsonl")])
    assert code == 2 and "cannot read" in err, err


@case("check missing file exits 2")
def t35():
    code, _, _ = run_cli(["check", os.path.join(FIX, "nope.jsonl"),
                          "--suite", "s", "--metric", "m"])
    assert code == 2


@case("verify malformed line exits 2")
def t36():
    path = wfile("bad.jsonl", "{oops\n")
    code, _, err = run_cli(["verify", path])
    assert code == 2 and "malformed" in err, err


@case("verify schema-invalid entry detected")
def t37():
    path = wfile("schema.jsonl", json.dumps({"ts": "bad", "suite": "s"}) + "\n")
    code, out, _ = run_cli(["verify", path])
    assert code == 1 and "schema-invalid" in out, out


@case("add to ledger with malformed lines exits 2")
def t38():
    path = wfile("mixed.jsonl", "{oops\n")
    code, _, err = run_cli(["add", path, "--suite", "s", "--metric", "m",
                            "--value", "1", "--ts", "2026-01-01"])
    assert code == 2, err


@case("no subcommand exits 2")
def t39():
    try:
        main([os.path.join(FIX, "x.jsonl")])
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert e.code == 2


@case("add missing --suite exits 2")
def t40():
    path = os.path.join(FIX, "x2.jsonl")
    code, _, _ = run_cli(["add", path, "--metric", "m", "--value", "1",
                          "--ts", "2026-01-01"])
    assert code == 2


@case("--help exits 0")
def t41():
    code, out, _ = run_cli(["--help"])
    assert code == 0 and "usage" in out.lower()


@case("--version exits 0")
def t42():
    code, out, _ = run_cli(["--version"])
    assert code == 0 and "0.1.1" in out


@case("unknown subcommand exits 2")
def t43():
    assert run_cli(["frobnicate"])[0] == 2


@case("verify --json shape")
def t44():
    path = build_ledger("j1.jsonl", entry("2026-01-01", "s", "m", 1.0))
    code, out, _ = run_cli(["verify", path, "--json"])
    data = json.loads(out)
    assert code == 0 and data["entries"] == 1 and data["findings"] == []


# ---------- [C] extreme edge ----------
@case("blank lines in ledger tolerated")
def t45():
    e = entry("2026-01-01", "s", "m", 1.0)
    e["hash"] = mod.entry_hash(mod.GENESIS, e)
    path = wfile("e1.jsonl", "\n" + json.dumps(e) + "\n\n")
    code, out, _ = run_cli(["verify", path])
    assert code == 0, out


@case("unicode in note and env values")
def t46():
    path = os.path.join(FIX, "u1.jsonl")
    code, _, _ = run_cli(["add", path, "--suite", "s", "--metric", "m",
                          "--value", "1", "--ts", "2026-01-01",
                          "--env", "机房=北京", "--note", "首发基准 🎉"])
    assert code == 0
    code, out, _ = run_cli(["verify", path])
    assert code == 0, out


@case("very large and tiny values")
def t47():
    path = os.path.join(FIX, "u2.jsonl")
    run_cli(["add", path, "--suite", "s", "--metric", "big", "--value", "1e300",
             "--ts", "2026-01-01"])
    code, _, _ = run_cli(["add", path, "--suite", "s", "--metric", "tiny",
                          "--value", "1e-300", "--ts", "2026-01-02"])
    assert code == 0
    code, out, _ = run_cli(["verify", path])
    assert code == 0, out


@case("negative values allowed")
def t48():
    path = os.path.join(FIX, "u3.jsonl")
    code, _, _ = run_cli(["add", path, "--suite", "s", "--metric", "loss",
                          "--value", "-2.5", "--ts", "2026-01-01"])
    assert code == 0


@case("1000-entry chain verify performance path")
def t49():
    entries = [entry("2026-01-01T%02d:%02d:00" % (i // 60 % 24, i % 60),
                     "s", "m", 1.0 + i * 0.001) for i in range(1000)]
    path = build_ledger("big.jsonl", *entries)
    code, out, _ = run_cli(["verify", path])
    assert code == 0 and "verified 1000 entries" in out, out


@case("drift on negative old value uses abs denominator")
def t50():
    path = build_ledger("u4.jsonl",
                        entry("2026-01-01", "s", "m", -100.0),
                        entry("2026-01-02", "s", "m", -80.0))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert code == 1 and "+20.0%" in out, out


@case("boolean value rejected by add")
def t51():
    path = os.path.join(FIX, "u5.jsonl")
    code, _, _ = run_cli(["add", path, "--suite", "s", "--metric", "m",
                          "--value", "1", "--ts", "2026-01-01"])
    assert code == 0
    # direct validate_entry check for bool
    assert mod.validate_entry(entry("2026-01-01", "s", "m", True))


@case("env with multiple pairs and spaces")
def t52():
    path = os.path.join(FIX, "u6.jsonl")
    code, _, _ = run_cli(["add", path, "--suite", "s", "--metric", "m",
                          "--value", "1", "--ts", "2026-01-01",
                          "--env", "os=win , python=3.12 , gpu=off"])
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["env"] == {"os": "win", "python": "3.12", "gpu": "off"}, data


@case("add note empty string omitted")
def t53():
    path = os.path.join(FIX, "u7.jsonl")
    run_cli(["add", path, "--suite", "s", "--metric", "m", "--value", "1",
             "--ts", "2026-01-01", "--note", ""])
    data = json.loads(open(path, encoding="utf-8").read())
    assert "note" not in data


@case("chain hashes differ across identical bodies with different prev")
def t54():
    e1 = entry("2026-01-01", "s", "m", 1.0)
    e2 = dict(e1)
    h1 = mod.entry_hash("GENESIS", e1)
    h2 = mod.entry_hash(h1, e2)
    assert h1 != h2


@case("repeated runs deterministic")
def t55():
    path = build_ledger("d1.jsonl", entry("2026-01-01", "s", "m", 1.0),
                        entry("2026-01-02", "s", "m", 1.5))
    r1 = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    r2 = run_cli(["check", path, "--suite", "s", "--metric", "m"])
    assert r1 == r2


@case("threshold boundary exactly equal passes")
def t56():
    path = build_ledger("d2.jsonl",
                        entry("2026-01-01", "s", "m", 100.0),
                        entry("2026-01-02", "s", "m", 105.0))
    code, _, _ = run_cli(["check", path, "--suite", "s", "--metric", "m",
                          "--threshold", "0.05"])
    assert code == 0


@case("threshold just beyond fails")
def t57():
    path = build_ledger("d3.jsonl",
                        entry("2026-01-01", "s", "m", 100.0),
                        entry("2026-01-02", "s", "m", 105.01))
    code, _, _ = run_cli(["check", path, "--suite", "s", "--metric", "m",
                          "--threshold", "0.05"])
    assert code == 1


@case("full pipeline: add add check verify report")
def t58():
    path = os.path.join(FIX, "flow.jsonl")
    run_cli(["add", path, "--suite", "bench", "--metric", "score",
             "--value", "100", "--ts", "2026-01-01",
             "--env", "os=win,py=3.11"])
    run_cli(["add", path, "--suite", "bench", "--metric", "score",
             "--value", "130", "--ts", "2026-01-02",
             "--env", "os=win,py=3.12"])
    code, out, _ = run_cli(["check", path, "--suite", "bench",
                            "--metric", "score"])
    assert code == 1 and "py: 3.11 -> 3.12" in out
    code, _, _ = run_cli(["verify", path])
    assert code == 0
    code, out, _ = run_cli(["report", path])
    assert code == 0 and "bench/score: 2 entr(ies), 100.0 -> 130.0" in out


@case("ts with timezone offset accepted")
def t59():
    path = os.path.join(FIX, "u8.jsonl")
    code, _, _ = run_cli(["add", path, "--suite", "s", "--metric", "m",
                          "--value", "1", "--ts", "2026-01-01T00:00:00+08:00"])
    assert code == 0


@case("CRLF ledger tolerated")
def t60():
    e = entry("2026-01-01", "s", "m", 1.0)
    e["hash"] = mod.entry_hash(mod.GENESIS, e)
    path = wfile("u9.jsonl", json.dumps(e) + "\r\n")
    code, out, _ = run_cli(["verify", path])
    assert code == 0, out


@case("BOM ledger tolerated")
def t61():
    e = entry("2026-01-01", "s", "m", 1.0)
    e["hash"] = mod.entry_hash(mod.GENESIS, e)
    path = os.path.join(FIX, "u10.jsonl")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(json.dumps(e) + "\n")
    code, out, _ = run_cli(["verify", path])
    assert code == 0, out


@case("multi-metric attribution isolates env diff")
def t62():
    path = build_ledger("u11.jsonl",
                        entry("2026-01-01", "s", "m1", 1.0, env={"a": "1"}),
                        entry("2026-01-02", "s", "m2", 5.0, env={"b": "2"}),
                        entry("2026-01-03", "s", "m1", 1.01, env={"a": "1"}))
    code, out, _ = run_cli(["check", path, "--suite", "s", "--metric", "m1"])
    assert code == 0 and "drift +1.00%" in out, out


@case("report sorted by suite then metric")
def t63():
    path = build_ledger("u12.jsonl",
                        entry("2026-01-01", "b", "z", 1.0),
                        entry("2026-01-02", "a", "y", 1.0))
    code, out, _ = run_cli(["report", path])
    assert out.index("a/y") < out.index("b/z"), out


@case("verify detects hash field removed")
def t64():
    path = build_ledger("u13.jsonl", entry("2026-01-01", "s", "m", 1.0))
    e = json.loads(open(path, encoding="utf-8").read())
    del e["hash"]
    path2 = wfile("u13b.jsonl", json.dumps(e) + "\n")
    code, out, _ = run_cli(["verify", path2])
    assert code == 1 and "chain-broken" in out, out


@case("summary wording stable across subcommands")
def t65():
    path = build_ledger("u14.jsonl", entry("2026-01-01", "s", "m", 1.0))
    _, out_v, _ = run_cli(["verify", path])
    _, out_r, _ = run_cli(["report", path])
    assert "verified 1 entries: 0 finding(s)" in out_v
    assert "reported 1 series: 0 parse error(s)" in out_r


class ToolTests(unittest.TestCase):
    pass


def _name(i, n):
    return "test_%03d_%s" % (i, re.sub(r"\W+", "_", n))


for _i, (_n, _fn) in enumerate(CASES):
    setattr(ToolTests, _name(_i, _n), lambda self, _f=_fn: _f())


if __name__ == "__main__":
    r = unittest.TextTestRunner(verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules["__main__"]))
    total, fails = r.testsRun, len(r.failures) + len(r.errors)
    for t, _ in r.failures + r.errors:
        print("FAIL:", t.id().split(".")[-1])
    print("SUMMARY: PASS %d/%d" % (total - fails, total))
    sys.exit(0 if fails == 0 else 1)
