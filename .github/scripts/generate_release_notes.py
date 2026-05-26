"""
generate_release_notes.py
─────────────────────────
Thu thập git log + diff stats từ PREV_TAG → CURRENT_TAG,
gửi cho AI model và lưu release notes ra /tmp/release_notes.md.

Env vars — chỉ cần set MỘT trong hai:
  GEMINI_API_KEY     – Google Gemini API key (FREE, khuyến nghị)
                       Tạo tại: https://aistudio.google.com/app/apikey
  ANTHROPIC_API_KEY  – Claude API key (trả phí, fallback)

  CURRENT_TAG        – tag vừa push (vd: 1.2.1)
  PREV_TAG           – tag liền trước (vd: 1.2.0), có thể rỗng nếu là release đầu tiên
"""

import json
import os
import subprocess
import sys
import urllib.request

# ─────────────────────────── helpers ────────────────────────────────────────

def run(cmd: list[str], check=False) -> str:
    """Chạy lệnh git, trả về stdout. Trả về chuỗi rỗng nếu lỗi."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def git_log(prev: str, current: str) -> str:
    """Lấy toàn bộ commit messages giữa hai tag."""
    range_spec = f"{prev}..{current}" if prev else current
    return run([
        "git", "log", range_spec,
        "--pretty=format:- %s%n%b",
        "--no-merges",
    ])


def git_diff_stat(prev: str, current: str, paths: list[str]) -> str:
    """Thống kê số dòng thay đổi theo nhóm path."""
    if not prev:
        return "(first release — no diff available)"
    return run(["git", "diff", "--stat", prev, current, "--"] + paths)


def read_version_h() -> str:
    """Đọc version từ firmware/uwb/app/version.h."""
    path = "firmware/uwb/app/version.h"
    try:
        with open(path) as f:
            lines = [l.strip() for l in f if "#define FW_VERSION_" in l]
        return "\n".join(lines)
    except FileNotFoundError:
        return "(version.h not found)"


def count_commits(prev: str, current: str) -> int:
    range_spec = f"{prev}..{current}" if prev else current
    out = run(["git", "rev-list", "--count", range_spec])
    try:
        return int(out)
    except ValueError:
        return 0


# ─────────────────────────── AI providers ───────────────────────────────────

def call_gemini(api_key: str, system: str, user: str) -> str:
    """Gọi Gemini 2.0 Flash (miễn phí) qua REST API — không cần thư viện ngoài."""
    model = "gemini-2.0-flash"
    print(f"Calling {model} (free tier)…")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models"
        f"/{model}:generateContent?key={api_key}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.3},
    }
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        print(f"Gemini API error {e.code}: {err_body}", file=sys.stderr)
        raise
    return body["candidates"][0]["content"]["parts"][0]["text"]


def call_claude(api_key: str, system: str, user: str) -> str:
    """Gọi Claude Haiku (rẻ ~$0.001/lần) qua REST API — không cần thư viện ngoài."""
    print("Calling Claude Haiku…")
    url  = "https://api.anthropic.com/v1/messages"
    payload = {
        "model": "claude-haiku-4-5",
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data, headers={
        "Content-Type":      "application/json",
        "x-api-key":         api_key,
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    return body["content"][0]["text"]


# ─────────────────────────── main ───────────────────────────────────────────

def main():
    gemini_key    = os.environ.get("GEMINI_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    current_tag   = os.environ.get("CURRENT_TAG", "unknown")
    prev_tag      = os.environ.get("PREV_TAG", "")

    if not gemini_key and not anthropic_key:
        print("ERROR: Set either GEMINI_API_KEY (free) or ANTHROPIC_API_KEY in GitHub Secrets.", file=sys.stderr)
        sys.exit(1)

    use_gemini = bool(gemini_key)   # Gemini được ưu tiên nếu có

    print(f"Generating release notes: {prev_tag or '<first>'} → {current_tag}")

    # ── Thu thập dữ liệu ────────────────────────────────────────────────────
    commits       = git_log(prev_tag, current_tag)
    n_commits     = count_commits(prev_tag, current_tag)
    version_info  = read_version_h()

    fw_stat       = git_diff_stat(prev_tag, current_tag, ["firmware/"])
    sw_stat       = git_diff_stat(prev_tag, current_tag, ["software/"])
    proto_stat    = git_diff_stat(prev_tag, current_tag, ["protocol/"])
    hw_stat       = git_diff_stat(prev_tag, current_tag, ["hardware/"])

    # ── Xây dựng prompt ─────────────────────────────────────────────────────
    system_prompt = """\
You are a technical writer for an embedded systems project called UWB-RTLS.
This is an Ultra-Wideband Real-Time Location System built around:
- STM32F411 microcontroller firmware (C, FreeRTOS) for UWB anchor and tag devices
- Python desktop software: GUI dashboard, simulation tools, programmer utility
- Protocol layer using Protocol Buffers (nanopb) over serial/USB
- BLE firmware and Raspberry Pi Zero gateway

Your task: write a clean, professional GitHub Release note in Markdown.
Rules:
- Group changes by area: Firmware, Software, Protocol, Hardware (only include areas with real changes)
- Use bullet points. Start each bullet with an emoji that matches the change type:
    🆕 new feature  🔧 improvement  🐛 bug fix  ⚡ performance  🔨 refactor  📝 docs/config
- If a commit message is vague like "wip" or "change config", infer the likely change from context but keep it brief
- Add a short 1–2 sentence summary paragraph at the top
- End with a "## 📦 Artifacts" section listing: firmware .hex/.bin and any software releases
- Do NOT include a "How to update" section unless there's a breaking change
- Write in English, keep it concise and factual
"""

    user_message = f"""\
Release: {prev_tag or "initial"} → {current_tag}
Total commits: {n_commits}

## Firmware version (version.h)
{version_info}

## Commit messages
{commits or "(no commits found)"}

## Diff statistics by area
### firmware/
{fw_stat or "(no changes)"}

### software/
{sw_stat or "(no changes)"}

### protocol/
{proto_stat or "(no changes)"}

### hardware/
{hw_stat or "(no changes)"}

Please write the release note now.
"""

    # ── Gọi AI API ───────────────────────────────────────────────────────────
    if use_gemini:
        release_notes = call_gemini(gemini_key, system_prompt, user_message)
    else:
        release_notes = call_claude(anthropic_key, system_prompt, user_message)

    # Thêm header với tag và link nếu chưa có
    header = f"# Release {current_tag}\n\n"
    if not release_notes.startswith("#"):
        release_notes = header + release_notes

    # ── Ghi ra file ──────────────────────────────────────────────────────────
    out_path = "/tmp/release_notes.md"
    with open(out_path, "w") as f:
        f.write(release_notes)

    print(f"✅ Release notes written to {out_path}")
    print("\n" + "─" * 60)
    print(release_notes)
    print("─" * 60)


if __name__ == "__main__":
    main()
