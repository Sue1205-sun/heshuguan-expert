"""命令行入口。

用法：
    python -m heshuguan check data/huachen.json          # 打印核验底稿
    python -m heshuguan check data/huachen.json -o out.md
    python -m heshuguan check data/huachen.json --json   # 结构化输出，供智能体调用
    python -m heshuguan metrics data/huachen.json        # 只看指标表
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import Case
from .worksheet import render, to_markdown


def load_case(path: Path) -> Case:
    if not path.exists():
        raise SystemExit(f"找不到文件：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON 解析失败：{exc}") from exc
    try:
        return Case.from_dict(payload)
    except (ValueError, TypeError) as exc:
        raise SystemExit(f"数据结构不符合要求：{exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heshuguan",
        description="核数官核验引擎。对财务数据做勾稽校验与红旗扫描，输出可复算的核验底稿。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="执行全部检查并生成底稿")
    check.add_argument("case", type=Path, help="案例 JSON 文件路径")
    check.add_argument("-o", "--out", type=Path, help="写入文件，默认打印到终端")
    check.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")

    metrics = sub.add_parser("metrics", help="只输出指标对比表")
    metrics.add_argument("case", type=Path, help="案例 JSON 文件路径")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    case = load_case(args.case)

    if args.command == "metrics":
        from .worksheet import _metric_table

        print(_metric_table(case))
        return 0

    if args.json:
        text = json.dumps(render(case), ensure_ascii=False, indent=2)
    else:
        text = to_markdown(case)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
