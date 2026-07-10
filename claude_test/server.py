#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backend service for the external-data testing workbench.

Run:
  python server.py

AI API config:
  Edit api_config.json in this directory.
  Environment variables AI_API_URL / AI_API_KEY / AI_MODEL can override it.
"""

from __future__ import annotations

import csv
import html
import io
import json
import math
import os
import re
import sys
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib import request, error
from xml.etree import ElementTree as ET

from agents.data_testing_agent import DataTestingAgent


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "api_config.json"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))
DATASETS: dict[str, dict[str, Any]] = {}
LAST_ANALYSIS: dict[str, dict[str, Any]] = {}
AGENT: DataTestingAgent | None = None

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

QUALITY_METRICS = [
    {"key": "query", "name": "查得率", "formula": "查得数 / 总样本数", "std": "≥70% 优，50-70% 可接受", "imp": "高", "good": 70, "warn": 50, "higher_better": True},
    {"key": "hit", "name": "命中率", "formula": "查得命中数 / 查得数", "std": "视业务场景而定", "imp": "中", "good": 60, "warn": 40, "higher_better": True},
    {"key": "falseReject", "name": "误拒率", "formula": "查得命中且好客户 / 查得好客户", "std": "越低越好", "imp": "高", "good": 3, "warn": 8, "higher_better": False},
    {"key": "miss", "name": "缺失率", "formula": "缺失字段数 / 总字段数", "std": "≤5% 优", "imp": "中", "good": 5, "warn": 10, "higher_better": False},
    {"key": "consist", "name": "一致性率", "formula": "逻辑一致记录数 / 总记录数", "std": "≥95%", "imp": "高", "good": 95, "warn": 90, "higher_better": True},
]


def default_api_config_document() -> dict[str, Any]:
    return {
        "active": "ccswitch",
        "profiles": {
            "ccswitch": {
                "api_type": "anthropic",
                "ccswitch": {
                    "enabled": True,
                    "config_path": "",
                    "profile": "",
                },
                "model": "claude-3-5-sonnet-20241022",
                "temperature": 0.2,
                "max_tokens": 4096,
                "timeout_seconds": 60,
            },
            "anthropic": {
                "api_type": "anthropic",
                "base_url": "https://api.anthropic.com",
                "api_key": "",
                "auth_token": "",
                "model": "claude-3-5-sonnet-20241022",
                "anthropic_version": "2023-06-01",
                "temperature": 0.2,
                "max_tokens": 4096,
                "timeout_seconds": 60,
            },
            "openai_compatible": {
                "api_type": "openai",
                "api_url": "https://api.openai.com/v1/chat/completions",
                "api_key": "",
                "model": "gpt-4.1-mini",
                "temperature": 0.2,
                "timeout_seconds": 60,
            },
            "zhipu": {
                "api_type": "openai",
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                "api_key": "",
                "model": "glm-5.2",
                "temperature": 0.2,
                "timeout_seconds": 60,
            },
        },
    }


def deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    keys = list(base.keys()) + [key for key in override.keys() if key not in base]
    for key in keys:
        left = base.get(key)
        right = override.get(key)
        if isinstance(left, dict) and isinstance(right, dict):
            merged[key] = deep_merge_dicts(left, right)
        elif key in override:
            merged[key] = right
        else:
            merged[key] = left
    return merged


def normalize_api_config_document(raw: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_api_config_document()
    if not isinstance(raw, dict):
        return defaults
    if isinstance(raw.get("profiles"), dict):
        doc = deep_merge_dicts({k: v for k, v in defaults.items() if k != "profiles"}, {k: v for k, v in raw.items() if k != "profiles"})
        profiles: dict[str, dict[str, Any]] = {}
        raw_profiles = raw.get("profiles") or {}
        for name, profile in defaults["profiles"].items():
            if name in raw_profiles and isinstance(raw_profiles[name], dict):
                profiles[name] = deep_merge_dicts(profile, raw_profiles[name])
            else:
                profiles[name] = profile
        for name, profile in raw_profiles.items():
            if name not in profiles and isinstance(profile, dict):
                profiles[name] = profile
        doc["profiles"] = profiles
        if doc.get("active") not in profiles and profiles:
            doc["active"] = next(iter(profiles))
        return doc
    active = str(raw.get("active") or raw.get("profile") or raw.get("name") or raw.get("api_type") or "custom").strip() or "custom"
    profile = {k: v for k, v in raw.items() if k not in {"active", "profile", "name"}}
    doc = default_api_config_document()
    base_profile = doc["profiles"].get(active, {})
    doc["profiles"] = deep_merge_dicts({active: base_profile}, {active: profile})
    doc["active"] = active
    return doc


def load_api_config_document() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return default_api_config_document()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"api_config.json 格式错误：{exc}") from exc
    return normalize_api_config_document(raw)


def save_api_config_document(document: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_api_config_document(document)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def api_config_payload() -> dict[str, Any]:
    document = load_api_config_document()
    return {
        "document": document,
        "config": public_api_config(),
    }


def get_agent() -> DataTestingAgent:
    global AGENT
    if AGENT is None:
        AGENT = DataTestingAgent(sys.modules[__name__], DATASETS, LAST_ANALYSIS)
    return AGENT


def clean_value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def is_blank(value: Any) -> bool:
    return clean_value(value) == ""


def to_number(value: Any, blank_as: float | None = None) -> float | None:
    if is_blank(value):
        return blank_as
    try:
        n = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return n if math.isfinite(n) else None


def pct(numerator: float, denominator: float, digits: int = 1) -> float:
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, digits)


def status_of(metric: dict[str, Any], value: float) -> str:
    good = value >= metric["good"] if metric["higher_better"] else value <= metric["good"]
    warn = value >= metric["warn"] if metric["higher_better"] else value <= metric["warn"]
    return "good" if good else ("warn" if warn else "crit")


def status_label(status: str) -> str:
    return {"good": "优", "warn": "可接受", "crit": "不达标"}.get(status, "待复核")


def truthy_flag(value: Any) -> bool:
    return clean_value(value).lower() in {"1", "true", "yes", "y", "是", "查得", "命中", "hit", "success"}


def col_to_idx(ref: str) -> int:
    col = "".join(ch for ch in ref if ch.isalpha())
    n = 0
    for ch in col:
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def normalize_header(value: Any, index: int, seen: dict[str, int]) -> str:
    header = clean_value(value) or f"字段{index + 1}"
    if header in seen:
        seen[header] += 1
        header = f"{header}_{seen[header]}"
    else:
        seen[header] = 1
    return header


def looks_like_description_row(row: list[Any] | None, headers: list[str]) -> bool:
    if not row:
        return False
    text = [clean_value(v) for v in row if not is_blank(v)]
    if not text:
        return False
    chinese = sum(1 for value in text if re.search(r"[\u4e00-\u9fff]", value))
    numeric = sum(1 for value in text if to_number(value) is not None)
    return chinese >= max(2, len(headers) // 4) and numeric <= max(1, len(text) // 7)


def normalize_dataset(matrix: list[list[Any]], file_name: str, sheet_name: str = "") -> dict[str, Any]:
    rows = [row for row in matrix if any(not is_blank(v) for v in row)]
    if len(rows) < 2:
        raise ValueError("文件中没有可分析的数据行")
    seen: dict[str, int] = {}
    headers = [normalize_header(value, i, seen) for i, value in enumerate(rows[0])]
    start = 1
    descriptions: dict[str, str] = {}
    if len(rows) > 1 and looks_like_description_row(rows[1], headers):
        for i, value in enumerate(rows[1]):
            if i < len(headers) and not is_blank(value):
                descriptions[headers[i]] = clean_value(value)
        start = 2
    data_rows: list[dict[str, Any]] = []
    for row in rows[start:]:
        item = {header: (row[i] if i < len(row) else "") for i, header in enumerate(headers)}
        if any(not is_blank(item[h]) for h in headers):
            data_rows.append(item)
    if not data_rows:
        raise ValueError("文件中没有可分析的数据行")
    return {
        "file_name": file_name,
        "sheet_name": sheet_name,
        "uploaded_at": int(time.time()),
        "headers": headers,
        "descriptions": descriptions,
        "rows": data_rows,
    }


def decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_csv(raw: bytes) -> list[list[str]]:
    text = decode_csv(raw)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for si in root.findall(f"{NS_MAIN}si"):
        strings.append("".join((node.text or "") for node in si.iter(f"{NS_MAIN}t")))
    return strings


def first_sheet_path(zf: zipfile.ZipFile) -> tuple[str, str]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
    sheets = workbook.find(f"{NS_MAIN}sheets")
    if sheets is None or not list(sheets):
        raise ValueError("Excel 文件中没有工作表")
    sheet = list(sheets)[0]
    name = sheet.attrib.get("name", "Sheet1")
    rel_id = sheet.attrib.get(f"{NS_REL}id")
    target = rel_map.get(rel_id or "", "worksheets/sheet1.xml")
    if not target.startswith("xl/"):
        target = "xl/" + target.lstrip("/")
    return target, name


def parse_xlsx(raw: bytes) -> tuple[list[list[Any]], str]:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        shared = read_shared_strings(zf)
        sheet_path, sheet_name = first_sheet_path(zf)
        root = ET.fromstring(zf.read(sheet_path))
        matrix: list[list[Any]] = []
        for row in root.iter(f"{NS_MAIN}row"):
            values: dict[int, Any] = {}
            for cell in row.findall(f"{NS_MAIN}c"):
                ref = cell.attrib.get("r", "")
                idx = col_to_idx(ref) if ref else len(values)
                ctype = cell.attrib.get("t", "")
                value = ""
                if ctype == "inlineStr":
                    inline = cell.find(f"{NS_MAIN}is")
                    value = "" if inline is None else "".join((node.text or "") for node in inline.iter(f"{NS_MAIN}t"))
                else:
                    node = cell.find(f"{NS_MAIN}v")
                    value = "" if node is None else (node.text or "")
                    if ctype == "s" and value != "":
                        value = shared[int(value)]
                values[idx] = value
            if values:
                matrix.append([values.get(i, "") for i in range(max(values) + 1)])
    return matrix, sheet_name


def parse_file(file_name: str, raw: bytes) -> dict[str, Any]:
    lower = file_name.lower()
    if lower.endswith(".csv"):
        return normalize_dataset(parse_csv(raw), file_name, "CSV")
    if lower.endswith(".xlsx"):
        matrix, sheet_name = parse_xlsx(raw)
        return normalize_dataset(matrix, file_name, sheet_name)
    if lower.endswith(".xls"):
        raise ValueError("当前零依赖后端仅支持 CSV 和 XLSX；如需 XLS，请先另存为 XLSX 或安装扩展解析库")
    raise ValueError("仅支持 CSV、XLSX 文件")


def unique_values(dataset: dict[str, Any], column: str, limit: int = 80) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for row in dataset["rows"]:
        value = clean_value(row.get(column))
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
        if len(values) >= limit:
            break
    return values


def numeric_columns(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    total = len(dataset["rows"])
    for header in dataset["headers"]:
        non_blank = 0
        numeric = 0
        non_zero = 0
        max_value = 0.0
        unique = set()
        for row in dataset["rows"]:
            raw = row.get(header, "")
            if is_blank(raw):
                continue
            non_blank += 1
            unique.add(clean_value(raw))
            number = to_number(raw)
            if number is not None:
                numeric += 1
                if number != 0:
                    non_zero += 1
                max_value = max(max_value, number)
        if non_blank and numeric / non_blank >= 0.95:
            result.append({
                "name": header,
                "desc": dataset["descriptions"].get(header, ""),
                "non_blank": non_blank,
                "non_blank_rate": pct(non_blank, total),
                "numeric": numeric,
                "non_zero": non_zero,
                "max": max_value,
                "unique": len(unique),
            })
    return result


def choose_bad_value(dataset: dict[str, Any], y_col: str, preferred: str = "1") -> str:
    values = unique_values(dataset, y_col, 200)
    if preferred in values:
        return preferred
    if "1" in values:
        return "1"
    numeric = [(value, to_number(value)) for value in values]
    numeric = [(value, number) for value, number in numeric if number is not None]
    if numeric:
        return sorted(numeric, key=lambda item: item[1], reverse=True)[0][0]
    return values[0] if values else ""


def infer_mapping(dataset: dict[str, Any]) -> dict[str, str]:
    headers = dataset["headers"]
    lower_map = {h: h.lower() for h in headers}
    y_col = next((h for h in headers if lower_map[h] in {"flagy", "y_label", "y", "label", "target", "bad_flag", "is_bad"}), "")
    if not y_col:
        y_col = next((h for h in headers if re.search(r"(^|_)(y|label|target|bad|overdue|dpd)(_|$)", h, re.I)), "")
    bad_value = choose_bad_value(dataset, y_col) if y_col else ""
    query_col = next((h for h in headers if h != y_col and re.search(r"flag_apply|query|hit|查得|命中", h, re.I)), "")
    nums = [c for c in numeric_columns(dataset) if c["name"] not in {y_col, query_col}]
    total = len(dataset["rows"])

    def not_identifier(col: dict[str, Any]) -> bool:
        name = col["name"]
        if re.search(r"cus|customer|客户|id|编号|no$", name, re.I) and col["unique"] > total * 0.8:
            return False
        return True

    nums = [c for c in nums if not_identifier(c)]

    def best(cols: list[dict[str, Any]]) -> str:
        if not cols:
            return ""
        cols = sorted(cols, key=lambda c: (c["non_zero"], c["max"]), reverse=True)
        return cols[0]["name"]

    exact = next((c["name"] for c in nums if c["name"] == "apply_org_cnt_30d"), "")
    org = best([c for c in nums if re.search(r"orgnum|org_cnt|机构数|apply_org", c["name"] + " " + c["desc"], re.I)])
    broad = best([c for c in nums if re.search(r"apply|loan|alhc|借贷|申请", c["name"] + " " + c["desc"], re.I)])
    variable_col = exact or org or broad or best(nums)
    return {"yCol": y_col, "badValue": bad_value, "queryFlagCol": query_col, "variableCol": variable_col}


def is_bad(row: dict[str, Any], mapping: dict[str, str]) -> bool:
    return clean_value(row.get(mapping["yCol"])) == clean_value(mapping["badValue"])


def is_good(row: dict[str, Any], mapping: dict[str, str]) -> bool:
    value = clean_value(row.get(mapping["yCol"]))
    return value != "" and value != clean_value(mapping["badValue"])


def create_binning(values: list[dict[str, Any]], mapping: dict[str, str], total_bad: int, total_good: int) -> dict[str, Any] | None:
    nums = [item["value"] for item in values if item["value"] is not None]
    if not nums:
        return None
    min_value = min(nums)
    max_value = max(nums)
    all_int = all(float(v).is_integer() for v in nums)
    defs: list[tuple[str, Callable[[float], bool]]] = []
    if min_value >= 0 and all_int and max_value <= 100:
        defs = [
            ("0", lambda v: v == 0),
            ("1-2", lambda v: 1 <= v <= 2),
            ("3-5", lambda v: 3 <= v <= 5),
            ("6-10", lambda v: 6 <= v <= 10),
            ("10以上", lambda v: v > 10),
        ]
    else:
        sorted_nums = sorted(nums)
        qs = [sorted_nums[min(len(sorted_nums) - 1, int((len(sorted_nums) - 1) * p))] for p in (0, 0.2, 0.4, 0.6, 0.8, 1)]
        for i in range(5):
            low, high = qs[i], qs[i + 1]
            if i > 0 and high == qs[i - 1]:
                continue
            if i == 0:
                defs.append((f"≤{high:.2f}", lambda v, high=high: v <= high))
            elif i == 4:
                defs.append((f">{low:.2f}", lambda v, low=low: v > low))
            else:
                defs.append((f"{low:.2f}-{high:.2f}", lambda v, low=low, high=high: low < v <= high))

    bins: list[dict[str, Any]] = []
    for label, test in defs:
        items = [item for item in values if test(item["value"])]
        if not items:
            continue
        bad = sum(1 for item in items if is_bad(item["row"], mapping))
        good = sum(1 for item in items if is_good(item["row"], mapping))
        bins.append({
            "label": label,
            "count": len(items),
            "bad": bad,
            "good": good,
            "badRate": bad / len(items) if items else 0,
            "_test": test,
        })
    total = sum(b["count"] for b in bins)
    total_bin_bad = sum(b["bad"] for b in bins)
    display_bins = [{k: v for k, v in b.items() if k != "_test"} for b in bins]
    return {
        "varName": mapping["variableCol"],
        "bins": display_bins,
        "_bins": bins,
        "total": total,
        "totalBad": total_bin_bad,
        "overallBadRate": total_bin_bad / total if total else 0,
        "totalDatasetBad": total_bad,
        "totalDatasetGood": total_good,
    }


def create_cutoff_rules(values: list[dict[str, Any]], mapping: dict[str, str], total: int, total_bad: int, total_good: int) -> list[dict[str, Any]]:
    nums = sorted(set(item["value"] for item in values if item["value"] is not None and item["value"] > 0))
    if not nums or not total_bad:
        return []
    preferred = [t for t in (1, 2, 3, 5, 8, 10, 15, 20) if any(v >= t for v in nums) and t <= max(nums)]
    thresholds = (preferred or nums[:8])[:8]
    rules: list[dict[str, Any]] = []
    for threshold in thresholds:
        hit = [item for item in values if item["value"] >= threshold]
        if not hit:
            continue
        bad_hit = sum(1 for item in hit if is_bad(item["row"], mapping))
        good_hit = sum(1 for item in hit if is_good(item["row"], mapping))
        rejection = pct(len(hit), total)
        recall = pct(bad_hit, total_bad)
        precision = pct(bad_hit, len(hit))
        kill = pct(good_hit, total_good)
        score = recall * 0.55 + precision * 0.35 - rejection * 0.1 - kill * 0.45
        rules.append({
            "rule": f"{mapping['variableCol']} ≥ {threshold:g}",
            "t": threshold,
            "rej": rejection,
            "recall": recall,
            "prec": precision,
            "kill": kill,
            "score": round(score, 3),
            "level": "",
            "verdict": "按上传数据计算",
        })
    if not rules:
        return []
    best = max(rules, key=lambda r: r["score"])
    for rule in rules:
        if rule is best:
            rule["level"] = "recommend"
            rule["verdict"] = "综合效果最优，推荐复核"
        elif rule["kill"] >= 10:
            rule["level"] = "serious"
            rule["verdict"] = "误杀率偏高，需谨慎"
    return rules


def calc_iv(binning: dict[str, Any] | None, total_bad: int, total_good: int) -> float | None:
    if not binning or not total_bad or not total_good:
        return None
    bins = binning["_bins"]
    iv = 0.0
    for item in bins:
        good_dist = (item["good"] + 0.5) / (total_good + 0.5 * len(bins))
        bad_dist = (item["bad"] + 0.5) / (total_bad + 0.5 * len(bins))
        iv += (good_dist - bad_dist) * math.log(good_dist / bad_dist)
    return max(0.0, iv)


def calc_ks(values: list[dict[str, Any]], mapping: dict[str, str], total_bad: int, total_good: int) -> float | None:
    if not total_bad or not total_good:
        return None
    bad = good = 0
    ks = 0.0
    for item in sorted(values, key=lambda x: x["value"], reverse=True):
        if is_bad(item["row"], mapping):
            bad += 1
        elif is_good(item["row"], mapping):
            good += 1
        ks = max(ks, abs(bad / total_bad - good / total_good))
    return ks


def calc_auc(values: list[dict[str, Any]], mapping: dict[str, str], total_bad: int, total_good: int) -> float | None:
    if not total_bad or not total_good:
        return None
    sorted_values = sorted(values, key=lambda x: x["value"])
    rank = 1
    sum_bad_ranks = 0.0
    i = 0
    while i < len(sorted_values):
        j = i + 1
        while j < len(sorted_values) and sorted_values[j]["value"] == sorted_values[i]["value"]:
            j += 1
        avg_rank = (rank + rank + (j - i) - 1) / 2
        for k in range(i, j):
            if is_bad(sorted_values[k]["row"], mapping):
                sum_bad_ranks += avg_rank
        rank += j - i
        i = j
    auc = (sum_bad_ranks - total_bad * (total_bad + 1) / 2) / (total_bad * total_good)
    return max(0.0, min(1.0, auc))


def calc_psi(values: list[dict[str, Any]], binning: dict[str, Any] | None) -> float | None:
    if not binning or len(values) < 20:
        return None
    mid = len(values) // 2
    parts = [values[:mid], values[mid:]]

    def dist(part: list[dict[str, Any]]) -> list[float]:
        result = []
        for item in binning["_bins"]:
            count = sum(1 for row in part if item["_test"](row["value"]))
            result.append((count + 0.5) / (len(part) + 0.5 * len(binning["_bins"])))
        return result

    before, after = dist(parts[0]), dist(parts[1])
    return sum((after[i] - before[i]) * math.log(after[i] / before[i]) for i in range(len(before)))


def metric_status(key: str, value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "warn"
    if key == "iv":
        return "good" if value >= 0.1 else ("warn" if value >= 0.02 else "crit")
    if key == "ks":
        return "good" if value >= 0.2 else ("warn" if value >= 0.1 else "crit")
    if key == "auc":
        return "good" if value >= 0.7 else ("warn" if value >= 0.6 else "crit")
    if key == "psi":
        return "good" if value <= 0.1 else ("warn" if value <= 0.25 else "crit")
    return "warn"


def create_model_metrics(values: list[dict[str, Any]], binning: dict[str, Any] | None, mapping: dict[str, str], total_bad: int, total_good: int) -> list[dict[str, Any]]:
    iv = calc_iv(binning, total_bad, total_good)
    ks = calc_ks(values, mapping, total_bad, total_good)
    auc = calc_auc(values, mapping, total_bad, total_good)
    psi = calc_psi(values, binning)
    metrics = [
        ("iv", "IV 值", iv, "", "IV≥0.1 可考虑入模", "按上传数据分箱计算"),
        ("ks", "KS 值", ks, "", "KS≥0.2 可考虑入模", "好坏客户累计分布最大差异"),
        ("auc", "AUC/ROC", auc, "", "AUC≥0.7 可考虑入模", "以所选变量作为单变量评分"),
        ("psi", "稳定性 PSI", psi, "%", "PSI≤10% 稳定，>25% 需复核", "按数据前后半段计算"),
    ]
    result = []
    for key, name, value, unit, advice, desc in metrics:
        if value is None:
            display = "N/A"
            shown_unit = ""
        elif key == "psi":
            display = f"{value * 100:.1f}"
            shown_unit = "%"
        else:
            display = f"{value:.3f}"
            shown_unit = unit
        result.append({
            "key": key,
            "name": name,
            "value": value,
            "display": display,
            "unit": shown_unit,
            "advice": advice,
            "desc": desc,
            "status": metric_status(key, value),
        })
    return result


def external_numeric_columns(dataset: dict[str, Any], mapping: dict[str, str]) -> list[dict[str, Any]]:
    total = len(dataset["rows"])
    columns = []
    for col in numeric_columns(dataset):
        name = col["name"]
        if name in {mapping.get("yCol"), mapping.get("queryFlagCol")}:
            continue
        if re.search(r"cus|customer|客户|id|编号|no$|num$", name, re.I) and col["non_zero"] > total * 0.8:
            continue
        columns.append(col)
    return columns


def compute_quality(dataset: dict[str, Any], mapping: dict[str, str], values: list[dict[str, Any]], cutoff_rules: list[dict[str, Any]], total: int, total_good: int) -> dict[str, float]:
    if mapping.get("queryFlagCol"):
        query_count = sum(1 for row in dataset["rows"] if truthy_flag(row.get(mapping["queryFlagCol"])))
    else:
        query_count = sum(1 for item in values if item["value"] > 0)
    hit_count = sum(1 for item in values if item["value"] > 0)
    best = next((r for r in cutoff_rules if r.get("level") == "recommend"), cutoff_rules[0] if cutoff_rules else None)
    false_reject = 0.0
    if best:
        good_rows = [item for item in values if is_good(item["row"], mapping)]
        killed = sum(1 for item in good_rows if item["value"] >= best["t"])
        false_reject = pct(killed, len(good_rows))
    blank = cells = bad_numeric_rows = 0
    columns = external_numeric_columns(dataset, mapping)
    for row in dataset["rows"]:
        row_ok = True
        for col in columns:
            cells += 1
            raw = row.get(col["name"])
            if is_blank(raw):
                blank += 1
                continue
            number = to_number(raw)
            if number is None or number < 0:
                row_ok = False
        if not row_ok:
            bad_numeric_rows += 1
    return {
        "query": pct(query_count, total),
        "hit": pct(hit_count, query_count or total),
        "falseReject": round(false_reject, 1),
        "miss": pct(blank, cells),
        "consist": pct(total - bad_numeric_rows, total),
    }


def analyze_dataset(dataset: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    required = ["yCol", "badValue", "variableCol"]
    for key in required:
        if not mapping.get(key):
            raise ValueError(f"缺少字段映射：{key}")
    for key in ("yCol", "queryFlagCol", "variableCol"):
        if mapping.get(key) and mapping[key] not in dataset["headers"]:
            raise ValueError(f"字段不存在：{mapping[key]}")
    rows = dataset["rows"]
    total = len(rows)
    total_bad = sum(1 for row in rows if is_bad(row, mapping))
    total_good = sum(1 for row in rows if is_good(row, mapping))
    values = [{"row": row, "value": to_number(row.get(mapping["variableCol"]), 0)} for row in rows]
    values = [item for item in values if item["value"] is not None]
    binning = create_binning(values, mapping, total_bad, total_good)
    cutoff_rules = create_cutoff_rules(values, mapping, total, total_bad, total_good)
    quality = compute_quality(dataset, mapping, values, cutoff_rules, total, total_good)
    model_metrics = create_model_metrics(values, binning, mapping, total_bad, total_good)
    public_binning = None
    if binning:
        public_binning = {k: v for k, v in binning.items() if k != "_bins"}
    return {
        "dataset": {
            "fileName": dataset["file_name"],
            "sheetName": dataset["sheet_name"],
            "rows": total,
            "cols": len(dataset["headers"]),
        },
        "mapping": mapping,
        "sample": {
            "total": total,
            "totalBad": total_bad,
            "totalGood": total_good,
            "badPct": pct(total_bad, total),
        },
        "quality": quality,
        "qualityJudgement": {m["key"]: status_label(status_of(m, quality.get(m["key"], 0))) for m in QUALITY_METRICS},
        "binning": public_binning,
        "cutoffRules": cutoff_rules,
        "modelMetrics": model_metrics,
    }


def grade_analysis(analysis: dict[str, Any]) -> str:
    q = analysis["quality"]
    query_metric = next(m for m in QUALITY_METRICS if m["key"] == "query")
    query_status = status_of(query_metric, q.get("query", 0))
    good_model = sum(1 for m in analysis["modelMetrics"] if m["status"] == "good")
    if query_status == "good" and q.get("consist", 0) >= 95 and good_model >= 3:
        return "A（推荐采购）"
    if query_status != "crit" and good_model >= 2:
        return "B（建议小范围试用）"
    return "C（暂不推荐）"


def local_report_markdown(analysis: dict[str, Any]) -> str:
    sample = analysis["sample"]
    mapping = analysis["mapping"]
    quality = analysis["quality"]
    grade = grade_analysis(analysis)
    rec_rule = next((r for r in analysis["cutoffRules"] if r.get("level") == "recommend"), analysis["cutoffRules"][0] if analysis["cutoffRules"] else None)
    metric_lines = "\n".join(f"- {m['name']}：{m['display']}{m['unit']}，{m['status']}" for m in analysis["modelMetrics"])
    cutoff_text = "暂无可用 Cutoff 规则"
    if rec_rule:
        cutoff_text = f"{rec_rule['rule']}，拒绝率 {rec_rule['rej']}%，召回率 {rec_rule['recall']}%，精准率 {rec_rule['prec']}%，误杀率 {rec_rule['kill']}%。"
    return f"""# 外部数据测试报告

## 一、测试概述
数据文件：{analysis['dataset']['fileName']}

样本量：{sample['total']} 条；坏样本：{sample['totalBad']} 条；坏样本占比：{sample['badPct']}%。

标签口径：`{mapping['yCol']} = {mapping['badValue']}` 识别为坏样本。

核心变量：`{mapping['variableCol']}`。

## 二、数据质量评估
- 查得率：{quality['query']}%
- 命中率：{quality['hit']}%
- 误拒率：{quality['falseReject']}%
- 缺失率：{quality['miss']}%
- 一致性率：{quality['consist']}%

## 三、核心变量分析
所选变量已完成分箱分析。整体坏客户率为 {(analysis['binning']['overallBadRate'] * 100 if analysis.get('binning') else 0):.1f}%。

## 四、策略规则评估
推荐规则：{cutoff_text}

## 五、模型入模评估
{metric_lines}

## 六、综合结论
数据可用性评级：{grade}

建议：{('可进入正式采购和生产验证流程' if grade.startswith('A') else '建议先做灰度验证或补充样本后复测' if grade.startswith('B') else '暂不建议采购，需供应商补充覆盖度和一致性后复测')}。
"""


def markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        if stripped.startswith("# "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h2>{html.escape(stripped[2:])}</h2>")
        elif stripped.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3>{html.escape(stripped[3:])}</h3>")
        elif stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html.escape(stripped[2:])}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{html.escape(stripped)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def ai_payload_summary(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": analysis["dataset"],
        "mapping": analysis["mapping"],
        "sample": analysis["sample"],
        "quality": analysis["quality"],
        "binning": analysis["binning"],
        "cutoffRules": analysis["cutoffRules"],
        "modelMetrics": analysis["modelMetrics"],
        "grade": grade_analysis(analysis),
        "privacyNote": "仅聚合指标和字段名，无客户明细行。",
    }


def load_api_config_from_document(document: dict[str, Any] | None) -> dict[str, Any]:
    defaults = {
        "api_type": "anthropic",
        "api_url": "",
        "base_url": "",
        "api_key": "",
        "auth_token": "",
        "model": "gpt-4.1-mini",
        "max_tokens": 4096,
        "temperature": 0.2,
        "timeout_seconds": 60,
        "anthropic_version": "2023-06-01",
        "custom_headers": {},
    }
    config = defaults.copy()
    file_config = normalize_api_config_document(document)
    file_config = resolve_profile_config(file_config)
    config.update({k: v for k, v in file_config.items() if v is not None})
    ccswitch_config = load_ccswitch_config(config)
    if ccswitch_config:
        config.update({k: v for k, v in ccswitch_config.items() if v is not None and v != ""})
    if config.get("request_url") and not config.get("api_url"):
        config["api_url"] = config["request_url"]
    if config.get("endpoint") and not config.get("api_url"):
        config["api_url"] = config["endpoint"]
    if config.get("base_url") and not config.get("api_url"):
        api_type = str(config.get("api_type", "")).strip().lower()
        if api_type in {"openai", "chat_completions", "openai-compatible", "zhipu"}:
            config["api_url"] = str(config["base_url"]).rstrip("/") + "/chat/completions"
        else:
            config["api_url"] = anthropic_messages_url(str(config["base_url"]))
    if os.environ.get("AI_API_URL"):
        config["api_url"] = os.environ["AI_API_URL"]
    if os.environ.get("ANTHROPIC_BASE_URL") and not os.environ.get("AI_API_URL"):
        config["api_url"] = anthropic_messages_url(os.environ["ANTHROPIC_BASE_URL"])
    if os.environ.get("AI_API_KEY"):
        config["api_key"] = os.environ["AI_API_KEY"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        config["api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        config["auth_token"] = os.environ["ANTHROPIC_AUTH_TOKEN"]
    if os.environ.get("AI_MODEL"):
        config["model"] = os.environ["AI_MODEL"]
    if os.environ.get("ANTHROPIC_MODEL"):
        config["model"] = os.environ["ANTHROPIC_MODEL"]
    if os.environ.get("AI_TEMPERATURE"):
        config["temperature"] = float(os.environ["AI_TEMPERATURE"])
    if os.environ.get("AI_TIMEOUT_SECONDS"):
        config["timeout_seconds"] = int(os.environ["AI_TIMEOUT_SECONDS"])
    if os.environ.get("AI_API_TYPE"):
        config["api_type"] = os.environ["AI_API_TYPE"]
    config["api_url"] = str(config.get("api_url", "")).strip()
    config["base_url"] = str(config.get("base_url", "")).strip()
    config["api_key"] = str(config.get("api_key", "")).strip()
    config["auth_token"] = str(config.get("auth_token", "")).strip()
    config["model"] = str(config.get("model", "gpt-4.1-mini")).strip() or "gpt-4.1-mini"
    config["api_type"] = str(config.get("api_type", "anthropic")).strip().lower() or "anthropic"
    config["max_tokens"] = int(config.get("max_tokens", 4096))
    config["temperature"] = float(config.get("temperature", 0.2))
    config["timeout_seconds"] = int(config.get("timeout_seconds", 60))
    config["anthropic_version"] = str(config.get("anthropic_version", "2023-06-01")).strip() or "2023-06-01"
    if not isinstance(config.get("custom_headers"), dict):
        config["custom_headers"] = {}
    return config


def load_api_config() -> dict[str, Any]:
    return load_api_config_from_document(load_api_config_document())


def resolve_profile_config(raw: dict[str, Any]) -> dict[str, Any]:
    if "profiles" not in raw:
        return raw
    profiles = raw.get("profiles") or {}
    active = raw.get("active") or raw.get("current") or raw.get("profile") or raw.get("currentProfile")
    if not active and isinstance(profiles, dict) and profiles:
        active = next(iter(profiles))
    selected = profiles.get(active, {}) if isinstance(profiles, dict) else {}
    merged = {k: v for k, v in raw.items() if k != "profiles"}
    if isinstance(selected, dict):
        merged.update(selected)
    merged["active"] = active
    return merged


def anthropic_messages_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if not value:
        return ""
    if value.endswith("/v1/messages"):
        return value
    if value.endswith("/v1"):
        return value + "/messages"
    return value + "/v1/messages"


def normalize_env_config(env: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if env.get("ANTHROPIC_BASE_URL"):
        result["base_url"] = env["ANTHROPIC_BASE_URL"]
        result["api_url"] = anthropic_messages_url(str(env["ANTHROPIC_BASE_URL"]))
        result["api_type"] = "anthropic"
    if env.get("ANTHROPIC_API_KEY"):
        result["api_key"] = env["ANTHROPIC_API_KEY"]
    if env.get("ANTHROPIC_AUTH_TOKEN"):
        result["auth_token"] = env["ANTHROPIC_AUTH_TOKEN"]
    if env.get("ANTHROPIC_MODEL"):
        result["model"] = env["ANTHROPIC_MODEL"]
    if env.get("ANTHROPIC_SMALL_FAST_MODEL") and not result.get("model"):
        result["model"] = env["ANTHROPIC_SMALL_FAST_MODEL"]
    if env.get("OPENAI_BASE_URL"):
        result["base_url"] = env["OPENAI_BASE_URL"]
        result["api_url"] = str(env["OPENAI_BASE_URL"]).rstrip("/") + "/chat/completions"
        result["api_type"] = "openai"
    if env.get("OPENAI_API_KEY"):
        result["api_key"] = env["OPENAI_API_KEY"]
    if env.get("OPENAI_MODEL"):
        result["model"] = env["OPENAI_MODEL"]
    if env.get("API_TIMEOUT_MS"):
        result["timeout_seconds"] = max(1, int(env["API_TIMEOUT_MS"]) // 1000)
    return result


def load_ccswitch_config(base_config: dict[str, Any]) -> dict[str, Any]:
    ccswitch = base_config.get("ccswitch") if isinstance(base_config.get("ccswitch"), dict) else {}
    if ccswitch and ccswitch.get("enabled") is False:
        return {}
    path_value = os.environ.get("CCSWITCH_CONFIG_PATH") or ccswitch.get("config_path") or ccswitch.get("path")
    candidates: list[Path] = []
    if path_value:
        candidates.append(Path(str(path_value)).expanduser())
    candidates.extend([
        ROOT / "ccswitch.json",
        ROOT / ".ccswitch.json",
        ROOT / "ccswitch.config.json",
        Path.home() / ".ccswitch" / "config.json",
        Path.home() / ".claude" / "ccswitch.json",
        Path.home() / ".claude" / "ccswitch" / "config.json",
    ])
    config_path = next((p for p in candidates if p.exists()), None)
    if not config_path:
        return {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"ccswitch 配置读取失败：{config_path}，{exc}") from exc
    profile_name = os.environ.get("CCSWITCH_PROFILE") or ccswitch.get("profile") or raw.get("active") or raw.get("current") or raw.get("currentProfile")
    selected = raw
    profiles = raw.get("profiles") or raw.get("configs") or raw.get("providers")
    if isinstance(profiles, dict):
        if not profile_name and profiles:
            profile_name = next(iter(profiles))
        selected = profiles.get(profile_name, {})
    elif isinstance(profiles, list):
        if not profile_name and profiles:
            selected = profiles[0]
        else:
            selected = next((item for item in profiles if item.get("name") == profile_name or item.get("id") == profile_name), {})
    if not isinstance(selected, dict):
        return {}
    env = selected.get("env") if isinstance(selected.get("env"), dict) else {}
    normalized = normalize_env_config(env)
    direct = {
        "api_type": selected.get("api_type") or selected.get("type") or selected.get("provider"),
        "api_url": selected.get("api_url") or selected.get("request_url") or selected.get("endpoint"),
        "base_url": selected.get("base_url") or selected.get("baseURL") or selected.get("url"),
        "api_key": selected.get("api_key") or selected.get("apiKey") or selected.get("key"),
        "auth_token": selected.get("auth_token") or selected.get("authToken") or selected.get("token"),
        "model": selected.get("model"),
    }
    normalized.update({k: v for k, v in direct.items() if v})
    if normalized.get("base_url") and not normalized.get("api_url"):
        if str(normalized.get("api_type", "")).lower() in {"openai", "chat_completions", "openai-compatible", "zhipu"}:
            normalized["api_url"] = str(normalized["base_url"]).rstrip("/") + "/chat/completions"
        else:
            normalized["api_url"] = anthropic_messages_url(str(normalized["base_url"]))
            normalized["api_type"] = normalized.get("api_type") or "anthropic"
    if normalized:
        normalized["source"] = f"ccswitch:{config_path}"
        if profile_name:
            normalized["profile"] = profile_name
    return normalized


def public_api_config() -> dict[str, Any]:
    config = load_api_config()
    return {
        "api_type": config["api_type"],
        "api_url_configured": bool(config["api_url"]),
        "api_url": config["api_url"],
        "base_url": config["base_url"],
        "model": config["model"],
        "timeout_seconds": config["timeout_seconds"],
        "max_tokens": config["max_tokens"],
        "source": config.get("source") or ("api_config.json" if CONFIG_PATH.exists() else "environment/default"),
        "profile": config.get("profile") or config.get("active") or "",
    }


def call_ai_report_api(analysis: dict[str, Any]) -> tuple[str | None, str | None]:
    config = load_api_config()
    url = config["api_url"]
    if not url:
        return None, "api_config.json 未配置 api_url，使用本地规则报告"
    api_key = config["api_key"]
    model = config["model"]
    prompt = (
        "你是银行风控外部数据测试分析专家。请基于聚合指标生成中文测试报告，"
        "结构包含：测试概述、数据质量评估、核心变量分析、策略规则评估、模型入模评估、综合结论。"
        "不要编造未提供的数据，不要要求查看客户明细。"
    )
    if config["api_type"] in {"openai", "chat_completions", "openai-compatible", "zhipu"}:
        return call_openai_compatible_api(config, prompt, analysis)
    return call_anthropic_messages_api(config, prompt, analysis)


def call_openai_compatible_api(config: dict[str, Any], prompt: str, analysis: dict[str, Any]) -> tuple[str | None, str | None]:
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(ai_payload_summary(analysis), ensure_ascii=False)},
        ],
        "temperature": config["temperature"],
    }
    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"
    raw, err = post_json(config["api_url"], payload, headers, config["timeout_seconds"], config.get("custom_headers", {}))
    if err:
        return None, err
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    if isinstance(data, dict):
        choices = data.get("choices")
        if choices and isinstance(choices, list):
            msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = msg.get("content")
            if content:
                return content, None
        for key in ("report", "content", "text", "result"):
            if data.get(key):
                return str(data[key]), None
    return json.dumps(data, ensure_ascii=False, indent=2), None


def call_anthropic_messages_api(config: dict[str, Any], prompt: str, analysis: dict[str, Any]) -> tuple[str | None, str | None]:
    payload = {
        "model": config["model"],
        "max_tokens": config["max_tokens"],
        "temperature": config["temperature"],
        "system": prompt,
        "messages": [
            {"role": "user", "content": json.dumps(ai_payload_summary(analysis), ensure_ascii=False)},
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": config["anthropic_version"],
    }
    if config["api_key"]:
        headers["x-api-key"] = config["api_key"]
    if config["auth_token"]:
        headers["Authorization"] = f"Bearer {config['auth_token']}"
    raw, err = post_json(config["api_url"], payload, headers, config["timeout_seconds"], config.get("custom_headers", {}))
    if err:
        return None, err
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    if isinstance(data, dict):
        content = data.get("content")
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    text_parts.append(str(item["text"]))
            if text_parts:
                return "\n".join(text_parts), None
        if data.get("completion"):
            return str(data["completion"]), None
        for key in ("report", "content", "text", "result"):
            if isinstance(data.get(key), str):
                return data[key], None
    return json.dumps(data, ensure_ascii=False, indent=2), None


def call_openai_compatible_text_api(
    config: dict[str, Any],
    prompt: str,
    user_text: str,
    max_output_tokens: int | None = None,
) -> tuple[str | None, str | None]:
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": config["temperature"],
    }
    if max_output_tokens:
        payload["max_tokens"] = max_output_tokens
    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"
    raw, err = post_json(config["api_url"], payload, headers, config["timeout_seconds"], config.get("custom_headers", {}))
    if err:
        return None, err
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    if isinstance(data, dict):
        choices = data.get("choices")
        if choices and isinstance(choices, list):
            msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = msg.get("content")
            if content:
                return content, None
        for key in ("report", "content", "text", "result"):
            if data.get(key):
                return str(data[key]), None
    return json.dumps(data, ensure_ascii=False, indent=2), None


def call_anthropic_messages_text_api(
    config: dict[str, Any],
    prompt: str,
    user_text: str,
    max_output_tokens: int | None = None,
) -> tuple[str | None, str | None]:
    payload = {
        "model": config["model"],
        "max_tokens": max_output_tokens or config["max_tokens"],
        "temperature": config["temperature"],
        "system": prompt,
        "messages": [
            {"role": "user", "content": user_text},
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": config["anthropic_version"],
    }
    if config["api_key"]:
        headers["x-api-key"] = config["api_key"]
    if config["auth_token"]:
        headers["Authorization"] = f"Bearer {config['auth_token']}"
    raw, err = post_json(config["api_url"], payload, headers, config["timeout_seconds"], config.get("custom_headers", {}))
    if err:
        return None, err
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    if isinstance(data, dict):
        content = data.get("content")
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    text_parts.append(str(item["text"]))
            if text_parts:
                return "\n".join(text_parts), None
        if data.get("completion"):
            return str(data["completion"]), None
        for key in ("report", "content", "text", "result"):
            if isinstance(data.get(key), str):
                return data[key], None
    return json.dumps(data, ensure_ascii=False, indent=2), None


def call_ai_connection_test(document: dict[str, Any] | None, question: str) -> tuple[str | None, str | None, dict[str, Any]]:
    config = load_api_config_from_document(document)
    if not config["api_url"]:
        return None, "api_config.json 未配置 api_url", config
    prompt = "你是接口连通性测试助手。请用简短中文直接回答，不要展开分析。"
    user_text = clean_value(question) or "请简短回复：接口连通性正常。"
    if config["api_type"] in {"openai", "chat_completions", "openai-compatible", "zhipu"}:
        reply, err = call_openai_compatible_text_api(config, prompt, user_text, max_output_tokens=128)
    else:
        reply, err = call_anthropic_messages_text_api(config, prompt, user_text, max_output_tokens=128)
    return reply, err, config


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int, custom_headers: dict[str, Any] | None = None) -> tuple[str, str | None]:
    req_headers = dict(headers)
    req_headers.update({str(k): str(v) for k, v in (custom_headers or {}).items()})
    req = request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=req_headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace"), None
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return "", f"AI API HTTP {exc.code}: {detail[:500]}"
    except Exception as exc:  # noqa: BLE001
        return "", f"AI API 调用失败：{exc}"


def build_report(analysis: dict[str, Any]) -> dict[str, Any]:
    ai_text, ai_error = call_ai_report_api(analysis)
    source = "api" if ai_text else "local"
    markdown = ai_text or local_report_markdown(analysis)
    return {
        "source": source,
        "aiError": ai_error,
        "grade": grade_analysis(analysis),
        "markdown": markdown,
        "html": markdown_to_html(markdown),
        "sentToApi": ai_payload_summary(analysis) if source == "api" else None,
    }


def parse_multipart(content_type: str, body: bytes) -> dict[str, dict[str, Any]]:
    match = re.search(r"boundary=(?P<boundary>[^;]+)", content_type)
    if not match:
        raise ValueError("缺少 multipart boundary")
    boundary = match.group("boundary").strip().strip('"').encode("utf-8")
    result: dict[str, dict[str, Any]] = {}
    for part in body.split(b"--" + boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].rstrip(b"\r\n")
        if b"\r\n\r\n" not in part:
            continue
        raw_headers, data = part.split(b"\r\n\r\n", 1)
        headers = raw_headers.decode("utf-8", errors="replace").split("\r\n")
        disposition = next((h for h in headers if h.lower().startswith("content-disposition:")), "")
        params = dict(re.findall(r'(\w+)="([^"]*)"', disposition))
        name = params.get("name")
        if not name:
            continue
        result[name] = {"filename": params.get("filename", ""), "data": data}
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "ExternalDataWorkbench/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_error_json(self, message: str, status: int = 400) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/config":
            self.send_json({"ok": True, **api_config_payload()})
            return
        if path in {"/", ""}:
            path = "/外部数据测试SOP工作台.html"
        target = (ROOT / path.lstrip("/")).resolve()
        if not str(target).startswith(str(ROOT)) or not target.exists() or target.is_dir():
            self.send_error_json("Not found", 404)
            return
        content_type = "text/html; charset=utf-8" if target.suffix.lower() == ".html" else "application/octet-stream"
        raw = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/api/upload":
                self.handle_upload()
            elif self.path == "/api/analyze":
                self.handle_analyze()
            elif self.path in {"/api/ai-report", "/api/report"}:
                self.handle_report()
            elif self.path == "/api/config/test":
                self.handle_config_test()
            elif self.path == "/api/config":
                self.handle_config_save()
            else:
                self.send_error_json("Unknown API", 404)
        except Exception as exc:  # noqa: BLE001
            self.send_error_json(str(exc), 500)

    def json_payload(self) -> dict[str, Any]:
        return json.loads(self.read_body().decode("utf-8"))

    def handle_upload(self) -> None:
        parts = parse_multipart(self.headers.get("Content-Type", ""), self.read_body())
        file_part = parts.get("file")
        if not file_part:
            self.send_error_json("没有收到文件")
            return
        result = get_agent().upload(file_name=file_part["filename"], raw=file_part["data"])
        if not result.ok:
            self.send_error_json(result.error)
            return
        self.send_json({"ok": True, **(result.data or {})})

    def handle_analyze(self) -> None:
        payload = self.json_payload()
        dataset_id = payload.get("datasetId", "")
        mapping = payload.get("mapping")
        result = get_agent().analyze(dataset_id=dataset_id, mapping=mapping)
        if not result.ok:
            self.send_error_json(result.error)
            return
        self.send_json({"ok": True, **(result.data or {})})

    def handle_report(self) -> None:
        payload = self.json_payload()
        dataset_id = payload.get("datasetId", "")
        mapping = payload.get("mapping")
        result = get_agent().report(dataset_id=dataset_id, mapping=mapping)
        if not result.ok:
            self.send_error_json(result.error)
            return
        self.send_json({"ok": True, **(result.data or {})})

    def handle_config_save(self) -> None:
        payload = self.json_payload()
        document = payload.get("document")
        if not isinstance(document, dict):
            document = payload
        saved = save_api_config_document(document)
        self.send_json({"ok": True, **api_config_payload(), "saved": saved})

    def handle_config_test(self) -> None:
        payload = self.json_payload()
        document = payload.get("document")
        if document is not None and not isinstance(document, dict):
            self.send_error_json("document 必须是对象")
            return
        question = clean_value(payload.get("question"))
        reply, err, config = call_ai_connection_test(document, question)
        if err:
            self.send_error_json(err, 400)
            return
        self.send_json(
            {
                "ok": True,
                "reply": reply,
                "config": {
                    "api_type": config["api_type"],
                    "api_url": config["api_url"],
                    "base_url": config["base_url"],
                    "model": config["model"],
                    "source": config.get("source") or "document",
                    "profile": config.get("profile") or config.get("active") or "",
                },
            }
        )


def main() -> None:
    os.chdir(ROOT)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    api_config = load_api_config()
    print(f"External data workbench running at http://{HOST}:{PORT}/")
    print(f"AI config: {CONFIG_PATH}")
    print("AI API:", api_config["api_url"] or "not configured")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server")


if __name__ == "__main__":
    main()
