from pathlib import Path
import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description="拾语 · 本地微信 4.x 聊天存档")
    parser.add_argument("--source", type=Path, help="预填微信账号或 db_storage 路径")
    parser.add_argument("--data", type=Path, help="存档和导出目录")
    parser.add_argument("--archive", type=Path, help="启动时打开已有拾语 .sqlite 存档")
    parser.add_argument("--demo", action="store_true", help="创建并打开明确标记的演示存档")
    parser.add_argument("--self-test", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        from wxarchive.selftest import run
        return run(args.self_test)
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    config = base / "local.json"
    try:
        options = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
        if not isinstance(options, dict):
            options = {}
    except (OSError, ValueError):
        options = {}
    source = args.source or (Path(options["source"]) if options.get("source") else None)
    data = args.data or base / "data"
    archive = args.archive
    if args.demo:
        from wxarchive.demo import create_demo
        archive = create_demo(data / "demo")
    from wxarchive.simple_ui import run
    return run(data, source, archive)


if __name__ == "__main__":
    raise SystemExit(main())
