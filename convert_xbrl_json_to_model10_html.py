#!/usr/bin/env python3
"""
Convert XBRL-JSON factset to XBRL Model 1.0 format with HTML span ID valueSources.

Usage:
  python convert_xbrl_json_to_model10_html.py <source-json> <html-id-text-file> <output-json>
"""

import ast
import json
import re
import sys
from collections import defaultdict


HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def normalize_spaces(s):
    s = s.replace("\xa0", " ")
    s = s.replace("\u202f", " ")
    s = s.replace("\u2009", " ")
    return WHITESPACE_RE.sub(" ", s).strip()


def normalize_text_for_match(s):
    if not isinstance(s, str):
        return ""
    s = HTML_TAG_RE.sub(" ", s)
    s = normalize_spaces(s)
    return s


def parse_french_number(s):
    if not isinstance(s, str):
        return None
    s = normalize_spaces(s)
    if not s:
        return None
    # Keep only characters relevant to a French-formatted number.
    candidate = re.sub(r"[^0-9,\-\+\s]", "", s)
    candidate = candidate.strip()
    if not candidate:
        return None
    candidate = candidate.replace(" ", "")
    candidate = candidate.replace(",", ".")
    try:
        return float(candidate)
    except ValueError:
        return None


def looks_numeric_value(value):
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value.strip()))


def load_html_id_texts(path):
    id_to_text = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("ID: Text nodes"):
                continue
            if ":" not in line:
                continue
            elem_id, rhs = line.split(":", 1)
            elem_id = elem_id.strip()
            rhs = rhs.strip()
            if not elem_id or not rhs.startswith("["):
                continue
            try:
                parts = ast.literal_eval(rhs)
            except Exception:
                continue
            if not isinstance(parts, list):
                continue
            txt = "".join(str(p) for p in parts)
            txt = normalize_text_for_match(txt)
            if txt:
                id_to_text[elem_id] = txt
    return id_to_text


def build_indexes(id_to_text):
    text_to_ids = defaultdict(list)
    numeric_entries = []
    for elem_id, txt in id_to_text.items():
        text_to_ids[txt].append(elem_id)
        num = parse_french_number(txt)
        if num is not None:
            numeric_entries.append((elem_id, num))
    return text_to_ids, numeric_entries


def number_tolerance(decimals):
    tol = 1.0
    if isinstance(decimals, int) and decimals < 0:
        # decimal=-5 means reported precision at 100000; use half-step tolerance.
        tol = max(tol, (10 ** (-decimals)) / 2.0)
    return tol


def match_html_ids(value, decimals, text_to_ids, numeric_entries):
    matched_ids = []

    if looks_numeric_value(value):
        fact_num = float(value)
        scales = [1, 1e3, 1e6, 1e9, 1e12]
        tol = number_tolerance(decimals)
        for elem_id, base_num in numeric_entries:
            for scale in scales:
                if abs((base_num * scale) - fact_num) <= tol:
                    matched_ids.append(elem_id)
                    break
    else:
        norm = normalize_text_for_match(value)
        if norm in text_to_ids:
            matched_ids.extend(text_to_ids[norm])

    seen = set()
    deduped = []
    for elem_id in matched_ids:
        if elem_id not in seen:
            seen.add(elem_id)
            deduped.append(elem_id)
    return deduped


def convert_fact_to_model10_html(fact_id, fact_obj, text_to_ids, numeric_entries):
    value = fact_obj.get("value")
    decimals = fact_obj.get("decimals")
    dimensions = fact_obj.get("dimensions", {})

    fact_dimensions = {}
    for dim_key, dim_val in dimensions.items():
        if ":" not in dim_key:
            fact_dimensions[f"xbrl:{dim_key}"] = dim_val
        else:
            fact_dimensions[dim_key] = dim_val

    fact_name = f"converted:{fact_id}"
    fact_value_name = f"{fact_name}_val"

    model_fact = {
        "name": fact_name,
        "factDimensions": fact_dimensions,
    }

    span_ids = match_html_ids(str(value), decimals, text_to_ids, numeric_entries)

    if span_ids:
        model_fact["factValues"] = [
            {
                "name": fact_value_name,
                "valueSources": [
                    {
                        "properties": [
                            {
                                "property": "xbrl:htmlSpanId",
                                "value": span_ids,
                            }
                        ]
                    }
                ],
            }
        ]
    else:
        model_fact["factValues"] = [
            {
                "name": fact_value_name,
                "value": str(value),
            }
        ]

    return model_fact, bool(span_ids)


def convert_factset(source_json_path, html_id_text_path, output_json_path):
    print(f"Loading source JSON: {source_json_path}")
    with open(source_json_path, "r", encoding="utf-8") as f:
        source = json.load(f)

    print(f"Loading HTML ID text mappings: {html_id_text_path}")
    id_to_text = load_html_id_texts(html_id_text_path)
    text_to_ids, numeric_entries = build_indexes(id_to_text)
    print(f"  Loaded {len(id_to_text)} non-empty HTML text IDs")

    output = {
        "documentInfo": source.get("documentInfo", {}).copy(),
    }
    if "documentInfo" in output:
        output["documentInfo"]["documentType"] = "https://xbrl.org/2026/model"

    source_facts = source.get("facts", {})
    converted_facts = []
    mapped_count = 0

    print(f"Converting {len(source_facts)} facts...")
    for fact_id, fact_obj in source_facts.items():
        out_fact, mapped = convert_fact_to_model10_html(
            fact_id,
            fact_obj,
            text_to_ids,
            numeric_entries,
        )
        if mapped:
            mapped_count += 1
        converted_facts.append(out_fact)

    output["xbrlModel"] = {
        "name": "converted:loreal-2025-12-31-html",
        "facts": converted_facts,
    }

    print(f"Writing output: {output_json_path}")
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Done. Converted {len(converted_facts)} facts. Facts with htmlSpanId mappings: {mapped_count}")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    source_json = sys.argv[1]
    html_id_text = sys.argv[2]
    output_json = sys.argv[3]

    convert_factset(source_json, html_id_text, output_json)
