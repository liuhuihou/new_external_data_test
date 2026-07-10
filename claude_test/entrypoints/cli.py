from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import server
from entrypoints.sdk import WorkbenchSDK


def build_mapping(args: argparse.Namespace) -> dict[str, str] | None:
    if args.mapping_json:
        mapping = json.loads(args.mapping_json)
        if not isinstance(mapping, dict):
            raise ValueError("--mapping-json 必须是对象")
        return {str(k): str(v) for k, v in mapping.items() if v is not None}
    mapping = {
        "yCol": args.y_col,
        "badValue": args.bad_value,
        "queryFlagCol": args.query_flag_col,
        "variableCol": args.variable_col,
    }
    filtered = {k: v for k, v in mapping.items() if v}
    return filtered or None


def add_mapping_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mapping-json", default="", help="直接传入映射 JSON")
    parser.add_argument("--y-col", default="", help="Y 标签字段")
    parser.add_argument("--bad-value", default="", help="坏样本取值")
    parser.add_argument("--query-flag-col", default="", help="查询/命中标识字段")
    parser.add_argument("--variable-col", default="", help="核心分析变量字段")


def print_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def print_dataset_summary(result: dict[str, Any]) -> None:
    print(f"文件: {result.get('fileName', '')}")
    print(f"工作表: {result.get('sheetName', '') or '-'}")
    print(f"行数: {result.get('rows', 0)}")
    print(f"列数: {result.get('cols', 0)}")
    print(f"映射: {json.dumps(result.get('mapping', {}), ensure_ascii=False)}")


def print_analysis_summary(result: dict[str, Any]) -> None:
    analysis = result["analysis"]
    dataset = analysis["dataset"]
    print(f"文件: {dataset['file_name']}")
    print(f"行数: {dataset['row_count']}  列数: {len(dataset['headers'])}")
    print(f"评分: {analysis.get('grade', '')}")
    print(f"样本坏占比: {analysis['quality'].get('badPct', 0)}%")
    print(f"质量判定: {json.dumps(analysis.get('qualityJudgement', {}), ensure_ascii=False)}")
    print(f"映射: {json.dumps(analysis.get('mapping', {}), ensure_ascii=False)}")


def cmd_serve(args: argparse.Namespace) -> int:
    server.HOST = args.host
    server.PORT = args.port
    server.main()
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    if args.config_action == "show":
        print_json(WorkbenchSDK().public_api_config())
        return 0
    if args.config_action == "path":
        print(str(server.CONFIG_PATH))
        return 0
    raise ValueError(f"未知 config 子命令: {args.config_action}")


def cmd_upload(args: argparse.Namespace) -> int:
    sdk = WorkbenchSDK()
    result = sdk.upload_path(args.file)
    if not result.ok or not result.data:
        print(result.error, file=sys.stderr)
        return 1
    if args.json:
        print_json(result.data)
    else:
        print_dataset_summary(result.data)
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    sdk = WorkbenchSDK()
    mapping = build_mapping(args)
    result = sdk.analyze_path(args.file, mapping=mapping)
    if not result.ok or not result.data:
        print(result.error, file=sys.stderr)
        return 1
    if args.json:
        print_json(result.data)
    else:
        print_analysis_summary(result.data)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    sdk = WorkbenchSDK()
    mapping = build_mapping(args)
    result = sdk.report_path(args.file, mapping=mapping)
    if not result.ok or not result.data:
        print(result.error, file=sys.stderr)
        return 1
    report = result.data["report"]
    if args.markdown:
        Path(args.markdown).expanduser().write_text(report["markdown"], encoding="utf-8")
    if args.html:
        Path(args.html).expanduser().write_text(report["html"], encoding="utf-8")
    if args.json:
        print_json(result.data)
    else:
        print(report["markdown"])
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from entrypoints.mcp import serve_stdio

    serve_stdio()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="external-data-workbench")
    parser.add_argument("--host", default=server.HOST, help="服务监听地址")
    parser.add_argument("--port", type=int, default=server.PORT, help="服务监听端口")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="启动网页服务")
    serve.set_defaults(func=cmd_serve)

    config = subparsers.add_parser("config", help="查看配置")
    config_sub = config.add_subparsers(dest="config_action", required=True)
    config_show = config_sub.add_parser("show", help="输出当前公开配置")
    config_show.set_defaults(func=cmd_config)
    config_path = config_sub.add_parser("path", help="输出配置文件路径")
    config_path.set_defaults(func=cmd_config)

    upload = subparsers.add_parser("upload", help="上传并解析文件")
    upload.add_argument("file", help="CSV 或 XLSX 文件路径")
    upload.add_argument("--json", action="store_true", help="输出 JSON")
    upload.set_defaults(func=cmd_upload)

    analyze = subparsers.add_parser("analyze", help="上传并分析文件")
    analyze.add_argument("file", help="CSV 或 XLSX 文件路径")
    add_mapping_args(analyze)
    analyze.add_argument("--json", action="store_true", help="输出 JSON")
    analyze.set_defaults(func=cmd_analyze)

    report = subparsers.add_parser("report", help="生成报告")
    report.add_argument("file", help="CSV 或 XLSX 文件路径")
    add_mapping_args(report)
    report.add_argument("--markdown", default="", help="保存 Markdown 路径")
    report.add_argument("--html", default="", help="保存 HTML 路径")
    report.add_argument("--json", action="store_true", help="输出 JSON")
    report.set_defaults(func=cmd_report)

    mcp = subparsers.add_parser("mcp", help="以 MCP 兼容方式输出工具接口")
    mcp.set_defaults(func=cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args.command = "serve"
        args.func = cmd_serve
    return int(args.func(args))
