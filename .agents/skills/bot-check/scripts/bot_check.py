"""발쫀봇 패치 후 점검: python .agents/skills/bot-check/scripts/bot_check.py [--base REF]

bot.py를 실행하거나 import하지 않습니다. 소스는 ast/compile로만 읽고,
테스트는 tests/의 가짜 DB·Discord 응답을 사용합니다. 파일을 수정하지 않으며
(--update-pyright-baseline 제외) 커밋·푸시·배포도 하지 않습니다.
"""

import argparse
import ast
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


SKILL_DIR = Path(__file__).resolve().parents[1]
ROOT = SKILL_DIR.parents[2]
BOT = ROOT / "bot.py"
PYRIGHT_BASELINE = SKILL_DIR / "pyright-baseline.json"
DEFAULT_TOOLKIT = ROOT.parent / "Valjjonjam-toolkit" / "script.js"

# AI_CONTEXT.md 4장의 공개 미러 필드. 비밀 필드는 미러에 있으면 실패로 처리한다.
MIRROR_FIELDS = {
    "kind", "source_id", "guild_id", "name", "scheduled_at", "status", "max_members",
    "member_ids", "waitlist_ids", "preferred_tier", "tier_restriction",
    "created_at", "updated_at",
}
SECRET_FIELDS = {"prize", "rules", "has_entry_fee", "entry_fee", "bank_holder", "account_number"}
# 웹과 공유하는 데이터·기존 모집글 버튼에 영향을 줄 수 있는 변경 줄을 찾는 단어
WATCH_TOKENS = (
    "tier_restriction", "waitlist_ids", "member_ids", "preferred_tier", "max_members",
    "internal_tier", "INTERNAL_TIER", "_COLLECTION", "toolkit_recruitment",
    "custom_id", "scheduled_at",
)

results = []  # (단계, "OK" | "WARN" | "FAIL" | "SKIP", 요약)


def record(step, state, summary):
    results.append((step, state, summary))
    print(f"[{state}] {summary}")


def run(args, **kwargs):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env, **kwargs,
    )


def heading(title):
    print(f"\n=== {title}")


# ① 문법 검사 — compile만 하고 실행하지 않는다.
def check_syntax():
    heading("① 문법 검사 (실행 없이 compile)")
    paths = [BOT, *sorted((ROOT / "tests").glob("*.py"))]
    failed = []
    for path in paths:
        try:
            compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
        except SyntaxError as error:
            failed.append(f"{path.relative_to(ROOT)}:{error.lineno} {error.msg}")
    if failed:
        record("syntax", "FAIL", "문법 오류: " + "; ".join(failed))
    else:
        record("syntax", "OK", f"{len(paths)}개 파일 문법 통과")


def check_diff_whitespace():
    result = run(["git", "diff", "--check"])
    if result.returncode == 0:
        record("syntax", "OK", "git diff --check 통과")
    else:
        record("syntax", "FAIL", "git diff --check 실패\n" + result.stdout.strip())


def check_pyright():
    pyright = shutil.which("pyright")
    if not pyright:
        record("pyright", "SKIP", "pyright 미설치 — 설치는 사용자 승인 후 진행")
        return None
    result = run([pyright, "--outputjson", "bot.py"])
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        record("pyright", "WARN", "pyright 결과를 읽지 못함\n" + result.stderr.strip()[-500:])
        return None
    diagnostics = [
        d for d in report.get("generalDiagnostics", [])
        if d.get("severity") in ("error", "warning")
    ]
    signatures = Counter(
        f"{d.get('severity')}|{d.get('rule', '')}|{d.get('message', '')}" for d in diagnostics
    )
    if not PYRIGHT_BASELINE.exists():
        record(
            "pyright", "WARN",
            f"기준선 없음: 진단 {len(diagnostics)}건 — --update-pyright-baseline로 기준선 저장",
        )
        return signatures
    baseline = Counter(json.loads(PYRIGHT_BASELINE.read_text(encoding="utf-8")))
    new = signatures - baseline
    if not new:
        record("pyright", "OK", f"기준선 대비 새 진단 없음 (전체 {len(diagnostics)}건)")
        return signatures
    shown = []
    for d in diagnostics:
        key = f"{d.get('severity')}|{d.get('rule', '')}|{d.get('message', '')}"
        if new[key] > 0:
            new[key] -= 1
            line = d.get("range", {}).get("start", {}).get("line", -1) + 1
            shown.append(f"  bot.py:{line} [{d.get('severity')}] {d.get('message', '').splitlines()[0]}")
    record("pyright", "WARN", f"기준선 대비 새 진단 {len(shown)}건\n" + "\n".join(shown[:20]))
    return signatures


# ② 가짜 DB 회귀 테스트
def check_tests():
    heading("② 가짜 DB 회귀 테스트")
    result = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], timeout=300)
    output = (result.stdout + result.stderr).strip()
    summary = [line for line in output.splitlines() if line.startswith(("Ran ", "OK", "FAILED"))]
    if result.returncode == 0:
        record("tests", "OK", " / ".join(summary) or "테스트 통과")
    else:
        record("tests", "FAIL", "테스트 실패\n" + "\n".join(output.splitlines()[-40:]))


# ③ 웹과 공유하는 계약 확인
def snapshot(source):
    """bot.py 소스에서 웹·기존 메시지와 공유하는 값을 실행 없이 뽑는다."""
    tree = ast.parse(source)
    data = {"custom_ids": set(), "collections": {}}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("INTERNAL_TIER_SCORES", "INTERNAL_TIER_LABELS", "EVENT_TIER_MODES"):
                data[name] = ast.literal_eval(node.value)
            elif name.endswith("_COLLECTION") and isinstance(node.value, ast.Constant):
                data["collections"][name] = node.value.value
        elif isinstance(node, ast.FunctionDef) and node.name == "build_toolkit_recruitment_data":
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and isinstance(child.value, ast.Dict):
                    data["mirror_keys"] = {
                        key.value for key in child.value.keys if isinstance(key, ast.Constant)
                    }
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "custom_id" and isinstance(node.value, ast.Constant):
            data["custom_ids"].add(node.value.value)
    return data


def compare(label, before, after):
    if before == after:
        return None
    if isinstance(before, dict) and isinstance(after, dict):
        lines = []
        for key in sorted(set(before) | set(after), key=str):
            if before.get(key) != after.get(key):
                lines.append(f"  {key}: {before.get(key, '(없음)')} → {after.get(key, '(없음)')}")
        return f"{label} 변경\n" + "\n".join(lines)
    removed = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    return f"{label} 변경 — 제거: {removed or '없음'}, 추가: {added or '없음'}"


def check_contract(base, toolkit_path):
    heading(f"③ 웹 공유 필드·티어 점수·버튼 ID (기준: {base})")
    current = snapshot(BOT.read_text(encoding="utf-8-sig"))

    mirror = current.get("mirror_keys", set())
    leaked = mirror & SECRET_FIELDS
    if leaked:
        record("contract", "FAIL", f"공개 미러에 비밀 필드 포함: {sorted(leaked)}")
    elif mirror != MIRROR_FIELDS:
        record(
            "contract", "WARN",
            f"미러 필드가 AI_CONTEXT.md와 다름 — 추가: {sorted(mirror - MIRROR_FIELDS)}, "
            f"누락: {sorted(MIRROR_FIELDS - mirror)}",
        )
    else:
        record("contract", "OK", "공개 미러 필드가 문서와 일치하고 비밀 필드 없음")

    base_source = run(["git", "show", f"{base}:bot.py"])
    if base_source.returncode != 0:
        record("contract", "SKIP", f"{base}의 bot.py를 읽지 못해 변경 전후 비교 생략")
    else:
        previous = snapshot(base_source.stdout.lstrip("﻿"))
        changes = [
            compare("내부 티어 점수 INTERNAL_TIER_SCORES", previous.get("INTERNAL_TIER_SCORES", {}), current.get("INTERNAL_TIER_SCORES", {})),
            compare("내부 티어 이름 INTERNAL_TIER_LABELS", previous.get("INTERNAL_TIER_LABELS", {}), current.get("INTERNAL_TIER_LABELS", {})),
            compare("이벤트 제한 방식 EVENT_TIER_MODES", previous.get("EVENT_TIER_MODES", {}), current.get("EVENT_TIER_MODES", {})),
            compare("컬렉션 이름", previous["collections"], current["collections"]),
            compare("공개 미러 필드", previous.get("mirror_keys", set()), mirror),
        ]
        changes = [change for change in changes if change]
        if changes:
            record("contract", "WARN", "웹 담당자 전달 필요\n" + "\n".join(changes))
        else:
            record("contract", "OK", "티어 점수·이름, 제한 방식, 컬렉션, 미러 필드 변경 없음")

        removed_ids = sorted(previous["custom_ids"] - current["custom_ids"])
        if removed_ids:
            record("contract", "WARN", f"기존 모집글 버튼 custom_id 제거·변경: {removed_ids} — 재시작 후 옛 버튼이 작동하지 않을 수 있음")
        else:
            record("contract", "OK", f"고정 버튼 custom_id {len(current['custom_ids'])}개 유지")

        diff = run(["git", "diff", "-U0", base, "--", "bot.py"])
        touched = []
        new_line = 0
        for line in diff.stdout.splitlines():
            hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            if hunk:
                new_line = int(hunk.group(1))
                continue
            if line.startswith(("+++", "---")):
                continue
            if line.startswith(("+", "-")):
                lowered = line.lower()
                if any(token.lower() in lowered for token in WATCH_TOKENS):
                    touched.append(f"  bot.py:{new_line} {line[:110]}")
                if line.startswith("+"):
                    new_line += 1
        if touched:
            record(
                "contract", "WARN",
                f"공유 필드·버튼 관련 줄 {len(touched)}곳 변경 — 의도한 변경인지 확인\n" + "\n".join(touched[:30]),
            )

    if not toolkit_path.exists():
        record("contract", "SKIP", f"웹 파일 없음: {toolkit_path} — 점수 일치 확인 생략")
        return
    web_text = toolkit_path.read_text(encoding="utf-8-sig")
    block = re.search(r"const INTERNAL_TIER_INFO = \{(.*?)\n\};", web_text, re.S)
    if not block:
        record("contract", "WARN", "웹 script.js에서 INTERNAL_TIER_INFO를 찾지 못함")
        return
    web_scores = {
        key: int(score)
        for key, score in re.findall(r'"([^"]+)"\s*:\s*\{[^}]*score:\s*(\d+)', block.group(1))
    }
    mismatch = compare("내부 티어 점수 불일치 (웹 → 봇)", web_scores, current.get("INTERNAL_TIER_SCORES", {}))
    if mismatch:
        record("contract", "WARN", mismatch + "\n  웹 코드는 수정하지 말고 웹 담당자에게 전달")
    else:
        record("contract", "OK", f"웹 INTERNAL_TIER_INFO와 점수 {len(web_scores)}개 일치")


def main():
    parser = argparse.ArgumentParser(description="발쫀봇 패치 후 점검 (실행·배포 없음)")
    parser.add_argument("--base", default="HEAD", help="변경 전으로 볼 Git 기준 (기본: HEAD)")
    parser.add_argument("--toolkit", type=Path, default=DEFAULT_TOOLKIT, help="웹 script.js 경로")
    parser.add_argument("--update-pyright-baseline", action="store_true", help="현재 pyright 진단을 기준선으로 저장")
    args = parser.parse_args()

    check_syntax()
    check_diff_whitespace()
    signatures = check_pyright()
    if args.update_pyright_baseline:
        if signatures is None:
            print("pyright 결과가 없어 기준선을 저장하지 않았습니다.")
        else:
            PYRIGHT_BASELINE.write_text(
                json.dumps(dict(sorted(signatures.items())), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"기준선 저장: {PYRIGHT_BASELINE.relative_to(ROOT)}")
    check_tests()
    check_contract(args.base, args.toolkit)

    heading("요약")
    counts = Counter(state for _, state, _ in results)
    print(" ".join(f"{state} {counts[state]}" for state in ("OK", "WARN", "FAIL", "SKIP")))
    print("커밋·푸시·서버 배포는 이 점검에 포함되지 않습니다.")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
