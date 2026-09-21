"""Check one K2 collection day end to end, in a single command.

Answers the questions that would otherwise be asked one at a time during the field day:
  C0   - did the board's anchor layout actually get recorded, and what is it?
  CFG  - which firmware configuration produced this session (A / B / C)?
  FILE - is every recording listed in the session log really on disk, non-empty and readable?
  LINK - did all four anchors keep ranging, or did one drop out for more than 10 s?
  PLAN - which of the 28 planned rows were done, which were marked failed, which are missing?

Usage (field day):
    python trust_check_session.py --session <SES_...> --fusion-dir <software/data/studio/dd_mm_yy> \
                                  --plan <SO_PHIEN_K2_PHIEN.csv> --out-json bien_ban.json

Self-check without field data:
    python trust_check_session.py --self-test

Read only: never writes into the session or fusion folders.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MAX_ANCHOR_GAP_S = 10.0     # K2 §7: "không mất mốc quá 10 giây"
MIN_STATIC_S = 60.0         # K2 §5.1.3: 90 s recorded, >= 60 s must survive trimming
EXPECTED_PLAN_ROWS = 28     # K2 bản 3 §5.5


# ----------------------------------------------------------------------------- helpers

def _fail(checks, code, msg):
    checks.append({"id": code, "ok": False, "msg": msg})


def _ok(checks, code, msg):
    checks.append({"id": code, "ok": True, "msg": msg})


def read_plan(path):
    """Read the pre-filled session log (SO_PHIEN_K2_PHIEN.csv)."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def parse_fusion(path):
    """Parse one Studio fusion export with P1's own reader, so this agrees with the dataset build."""
    from module.module_trust_dataset import build_text_recording

    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    name = os.path.basename(path)
    res = build_text_recording(name, name, lines)
    if res is None:
        return None
    return res


def anchor_gaps(links, steps):
    """Largest silence per anchor, in seconds, using the frame time axis when present."""
    import numpy as np
    import pandas as pd

    if links is None or len(links) == 0:
        return {}
    t = None
    if steps is not None and len(steps) and "frame_idx" in steps.columns:
        for col in ("t_s", "ts_ms"):
            if col in steps.columns and steps[col].notna().any():
                s = steps.dropna(subset=["frame_idx"])[["frame_idx", col]].drop_duplicates("frame_idx")
                t = dict(zip(s["frame_idx"].astype(int), pd.to_numeric(s[col], errors="coerce")))
                if col == "ts_ms":
                    t = {k: v / 1000.0 for k, v in t.items()}
                break
    out = {}
    valid = links[pd.to_numeric(links.get("d_m"), errors="coerce") > 0.05]
    for aid, g in valid.groupby("anchor_id"):
        fi = sorted(int(f) for f in g["frame_idx"].dropna().unique())
        if len(fi) < 2:
            out[int(aid)] = None
            continue
        if t:
            ts = [t.get(f) for f in fi]
            ts = [x for x in ts if x is not None and x == x]
            gaps = np.diff(ts) if len(ts) > 1 else [0.0]
        else:                                   # no time axis: assume the nominal 0.1 s frame
            gaps = np.diff(fi) * 0.1
        out[int(aid)] = float(max(gaps)) if len(gaps) else 0.0
    return out


# ----------------------------------------------------------------------------- checks

def check_session_messages(path, checks, report):
    """C0 evidence + which firmware configuration was actually running."""
    if not path or not os.path.exists(path):
        _fail(checks, "C0", f"khong thay session_messages.csv: {path}")
        return
    kinds = {}
    layout_rows = []
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            k = (row.get("message_type") or row.get("type") or row.get("name") or "").strip()
            if not k:
                blob = json.dumps(row)[:400]
                for cand in ("anchor_layout_resp", "device_information_resp", "range_diag"):
                    if cand in blob:
                        k = cand
                        break
            if not k:
                continue
            kinds[k] = kinds.get(k, 0) + 1
            if "anchor_layout" in k:
                layout_rows.append(row)
    report["message_counts"] = kinds

    if any("anchor_layout" in k for k in kinds):
        _ok(checks, "C0", f"co anchor_layout_resp ({sum(v for k, v in kinds.items() if 'anchor_layout' in k)} ban tin)")
        if layout_rows:
            report["anchor_layout_raw"] = json.dumps(layout_rows[0])[:1200]
    else:
        _fail(checks, "C0", "KHONG co anchor_layout_resp -> phien Studio mo SAU khi ket noi Tag. "
                            "Toa do moc tren bo khong duoc ghi lai; phai lam lai C0 truoc khi thu tiep.")

    if any("device_information" in k for k in kinds):
        _ok(checks, "DEV", "co device_information_resp")
    else:
        _fail(checks, "DEV", "khong co device_information_resp (khong biet commit firmware dang chay)")

    n_diag = sum(v for k, v in kinds.items() if "range_diag" in k)
    report["n_range_diag"] = n_diag
    if n_diag > 0:
        _ok(checks, "CFG", f"co {n_diag} ban tin range_diag -> cau hinh B hoac C, se co amp + thanh ghi")
    else:
        _fail(checks, "CFG", "KHONG co range_diag -> dang chay cau hinh A. Du lieu se KHONG co amp, "
                             "khong dung duoc cho P3. Kiem lai co SYS_RANGING_DIAG_STREAM_ENABLE khi build chua.")


def check_fusion_files(fusion_dir, checks, report):
    if not fusion_dir or not os.path.isdir(fusion_dir):
        _fail(checks, "FILE", f"khong thay thu muc fusion: {fusion_dir}")
        return {}
    files = sorted(f for f in os.listdir(fusion_dir) if f.lower().endswith(".csv"))
    if not files:
        _fail(checks, "FILE", f"thu muc {fusion_dir} khong co file .csv nao")
        return {}

    per_file = {}
    bad = []
    for name in files:
        path = os.path.join(fusion_dir, name)
        entry = {"rows": 0, "anchors": [], "max_gap_s": {}, "note": ""}
        try:
            res = parse_fusion(path)
            if res is None:
                entry["note"] = "khong nhan dang duoc dinh dang"
                bad.append(name)
            else:
                steps = getattr(res, "steps", None)
                links = getattr(res, "links", None)
                entry["rows"] = int(len(steps)) if steps is not None else 0
                if links is not None and len(links):
                    entry["anchors"] = sorted(int(a) for a in links["anchor_id"].dropna().unique())
                    gaps = anchor_gaps(links, steps)
                    entry["max_gap_s"] = {str(k): (None if v is None else round(v, 2)) for k, v in gaps.items()}
                    worst = [f"A{k}={v:.1f}s" for k, v in gaps.items() if v is not None and v > MAX_ANCHOR_GAP_S]
                    if worst:
                        entry["note"] = "MAT MOC > 10 s: " + ", ".join(worst)
                        bad.append(name)
                    missing = [a for a in (1, 2, 3, 4) if a not in entry["anchors"]]
                    if missing:
                        entry["note"] = (entry["note"] + " | " if entry["note"] else "") + \
                                        "thieu han moc: " + ", ".join(f"A{a}" for a in missing)
                        bad.append(name)
                else:
                    entry["note"] = "khong co cu ly nao"
                    bad.append(name)
        except Exception as exc:                                   # noqa: BLE001 - field tool, report not crash
            entry["note"] = f"loi doc: {type(exc).__name__}: {exc}"
            bad.append(name)
        per_file[name] = entry

    report["files"] = per_file
    if bad:
        _fail(checks, "LINK", f"{len(bad)}/{len(files)} file co van de: {', '.join(sorted(set(bad))[:6])}")
    else:
        _ok(checks, "LINK", f"{len(files)} file doc duoc, du 4 moc, khong mat moc qua {MAX_ANCHOR_GAP_S:.0f} s")


def check_plan(plan_path, fusion_dir, checks, report):
    if not plan_path or not os.path.exists(plan_path):
        _fail(checks, "PLAN", f"khong thay so phien: {plan_path}")
        return
    rows = read_plan(plan_path)
    report["plan_rows"] = len(rows)
    if len(rows) < EXPECTED_PLAN_ROWS:
        _fail(checks, "PLAN", f"so phien chi co {len(rows)} dong, ke hoach K2 ban 3 co {EXPECTED_PLAN_ROWS}")

    done, failed, empty, missing_file, dup = [], [], [], [], []
    seen = {}
    for r in rows:
        stt = (r.get("stt") or "?").strip()
        f = (r.get("file_fusion") or "").strip()
        k3 = (r.get("k3_ket_qua") or "").strip().lower()
        if not f:
            empty.append(stt)
            continue
        if k3.startswith("kh"):          # "Khong" / "Khong co do"
            failed.append(stt)
        else:
            done.append(stt)
        if f in seen:
            dup.append(f"{f} (dong {seen[f]} va {stt})")
        seen[f] = stt
        if fusion_dir and os.path.isdir(fusion_dir):
            hit = any(f in name or name in f for name in os.listdir(fusion_dir))
            if not hit:
                missing_file.append(f"dong {stt}: {f}")

    report["plan"] = {"done": done, "failed": failed, "not_recorded": empty,
                      "file_not_found": missing_file, "duplicate_file": dup}
    if dup:
        _fail(checks, "PLAN-DUP", f"hai dong tro cung mot file: {'; '.join(dup[:4])}")
    else:
        _ok(checks, "PLAN-DUP", "khong co dong nao tro trung file")
    if missing_file:
        _fail(checks, "PLAN-FILE", f"{len(missing_file)} dong ghi ten file khong tim thay: {'; '.join(missing_file[:4])}")
    else:
        _ok(checks, "PLAN-FILE", "moi ten file trong so phien deu ton tai")
    _ok(checks, "PLAN", f"da ghi {len(done)} dong dat, {len(failed)} dong danh dau khong dat, "
                        f"{len(empty)}/{len(rows)} dong chua thu")


# ----------------------------------------------------------------------------- self-test

def self_test() -> bool:
    ok = True
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
        from module.module_trust_dataset import build_text_recording  # noqa: F401
        print("[OK]   S0 dependency doc fusion: numpy + pandas + module_trust_dataset")
    except Exception as exc:  # noqa: BLE001 - field preflight must report every dependency failure
        print(f"[FAIL] S0 dependency doc fusion: {type(exc).__name__}: {exc}")
        ok = False

    with tempfile.TemporaryDirectory() as tmp:
        plan = os.path.join(tmp, "plan.csv")
        with open(plan, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["stt", "thi_nghiem", "file_fusion", "k3_ket_qua"])
            w.writerow(["1", "TN4-dau", "a.csv", "Dat"])
            w.writerow(["2", "TN1", "a.csv", "Dat"])          # trung file - phai bi bat
            w.writerow(["3", "TN1", "khong_co.csv", "Dat"])   # thieu file - phai bi bat
            w.writerow(["4", "TN1", "", ""])                  # chua thu
        fdir = os.path.join(tmp, "fusion")
        os.makedirs(fdir)
        open(os.path.join(fdir, "a.csv"), "w").close()

        checks, report = [], {}
        check_plan(plan, fdir, checks, report)
        by = {c["id"]: c for c in checks}
        for cid, want in (("PLAN-DUP", False), ("PLAN-FILE", False)):
            got = by.get(cid, {}).get("ok")
            print(f"[{'PASS' if got is want else 'FAIL'}] S1 {cid} bat dung loi co y gai: ok={got}")
            ok &= got is want
        print(f"[{'PASS' if report['plan']['not_recorded'] == ['4'] else 'FAIL'}] "
              f"S2 dong chua thu = {report['plan']['not_recorded']}")
        ok &= report["plan"]["not_recorded"] == ["4"]

        checks, report = [], {}
        check_session_messages(os.path.join(tmp, "khong_ton_tai.csv"), checks, report)
        got = [c for c in checks if c["id"] == "C0"][0]["ok"]
        print(f"[{'PASS' if got is False else 'FAIL'}] S3 thieu session_messages bi bao loi")
        ok &= got is False

        empty_msg = os.path.join(tmp, "session_messages.csv")
        with open(empty_msg, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["message_type", "payload"])
            w.writerow(["anchor_layout_resp", '{"anchors": 4}'])
            w.writerow(["device_information_resp", "{}"])
        checks, report = [], {}
        check_session_messages(empty_msg, checks, report)
        by = {c["id"]: c["ok"] for c in checks}
        print(f"[{'PASS' if by.get('C0') and by.get('DEV') and by.get('CFG') is False else 'FAIL'}] "
              f"S4 co layout+device nhung khong range_diag -> canh bao cau hinh A: {by}")
        ok &= bool(by.get("C0")) and bool(by.get("DEV")) and by.get("CFG") is False
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


# ----------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--session", help="thu muc phien Studio SES_... (hoac duong dan session_messages.csv)")
    ap.add_argument("--fusion-dir", help="thu muc chua cac file *_sensor_fusion_result.csv cua buoi thu")
    ap.add_argument("--plan", help="duong dan SO_PHIEN_K2_PHIEN.csv da dien")
    ap.add_argument("--out-json", help="ghi ket qua ra file JSON")
    args = ap.parse_args()

    if args.self_test:
        return 0 if self_test() else 1

    msg_path = args.session
    if msg_path and os.path.isdir(msg_path):
        msg_path = os.path.join(msg_path, "messages", "session_messages.csv")

    checks, report = [], {}
    check_session_messages(msg_path, checks, report)
    check_fusion_files(args.fusion_dir, checks, report)
    if args.plan:
        check_plan(args.plan, args.fusion_dir, checks, report)

    print("=" * 78)
    print("BIEN BAN KIEM BUOI THU K2")
    print("=" * 78)
    for c in checks:
        print(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['id']:10s} {c['msg']}")
    if report.get("files"):
        print("-" * 78)
        print(f"{'file':<46}{'dong':>7}{'moc':>10}  ghi chu")
        for name, e in report["files"].items():
            anchors = ",".join(str(a) for a in e["anchors"]) or "-"
            print(f"{name[:45]:<46}{e['rows']:>7}{anchors:>10}  {e['note']}")
    if report.get("anchor_layout_raw"):
        print("-" * 78)
        print("TOA DO MOC DOC TU BO (viec C0) - chep nguyen van cho Claude:")
        print(report["anchor_layout_raw"])

    n_bad = sum(1 for c in checks if not c["ok"])
    print("=" * 78)
    print(f"RESULT: {'PASS' if n_bad == 0 else f'FAIL ({n_bad} muc)'}")
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as fh:
            json.dump({"checks": checks, "report": report}, fh, indent=2, ensure_ascii=False)
        print(f"da ghi {args.out_json}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
