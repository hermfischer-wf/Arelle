#!/usr/bin/env python3
"""
Convert XBRL-JSON factset to XBRL Model 1.0 factset format with MCID mapping.

Usage:
  python convert_xbrl_json_to_model10.py <source-json> <mcid-file> <output-json> [<reference-model-json>]

The script transforms:
1. Facts from dict-based (XBRL-JSON) to object-based (Model 1.0) format
2. Dimensions become factDimensions properties
3. Values become factValues array entries
4. Where MCIDs map to fact values, valueSources is added using property-value objects
"""

import json
import sys
import re
from decimal import Decimal
from pathlib import Path
from collections import defaultdict


def load_mcids(mcid_file):
    """Load MCID mapping from text file (Python dict repr format)."""
    mcids = {}
    with open(mcid_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or ':' not in line:
                continue
            # Parse "p1R_mc0: {'txt': '...', 'pos': [...]}"
            match = re.match(r"(p\d+R_mc\d+):\s*\{", line)
            if not match:
                continue
            mcid_key = match.group(1)
            # Try to eval the dict part (risky but this is internal format)
            try:
                dict_str = line[line.index('{'):]
                mcid_data = eval(dict_str)
                mcids[mcid_key] = mcid_data
            except Exception as e:
                print(f"Warning: Could not parse MCID {mcid_key}: {e}", file=sys.stderr)
                continue
    return mcids


def normalize_french_number(text):
    """Convert French number format to standard decimal.
    
    E.g. "1 234,5" -> 1234.5
         "44 052,0" -> 44052.0
    """
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    # Remove spaces (thousand separators)
    text = text.replace(' ', '')
    # Replace comma with period (decimal separator)
    text = text.replace(',', '.')
    try:
        return float(text)
    except ValueError:
        return None


def find_mcids_for_value(value, decimals, mcids):
    """Find MCID keys that might correspond to a numeric fact value.
    
    Tries to match both direct values and scaled values (e.g., millions, billions).
    
    Args:
        value: fact value (str or number)
        decimals: decimals attribute if present
        mcids: dict of all loaded MCIDs
    
    Returns:
        list of (mcid_key, mcid_data) tuples that could represent this value
    """
    if not isinstance(value, str):
        return []
    
    # Try to normalize if it looks like a number
    try:
        numeric_val = float(value) if not isinstance(value, (int, float)) else value
    except (ValueError, TypeError):
        return []
    
    # Common scaling factors (thousands, millions, billions)
    scales = [1, 1e3, 1e6, 1e9, 1e12]
    tolerance = 1.0  # Allow small rounding differences
    
    matches = []
    for mcid_key, mcid_data in mcids.items():
        mcid_txt = mcid_data.get('txt', '').strip()
        # Try matching French-formatted numbers
        french_num = normalize_french_number(mcid_txt)
        if french_num is None:
            continue
        
        # Try each scale factor
        for scale in scales:
            scaled_mcid = french_num * scale
            if abs(scaled_mcid - numeric_val) < tolerance:
                matches.append((mcid_key, mcid_data))
                break  # Only match once per MCID
    
    return matches


def build_value_sources(mcid_matches):
    """Build property-value style valueSources and combine MCIDs per page."""
    page_to_mcids = defaultdict(list)

    for mcid_key, _mcid_data in mcid_matches:
        m = re.match(r'p(\d+)R_mc(\d+)', mcid_key)
        if not m:
            continue
        page = int(m.group(1))
        mcid = int(m.group(2))
        if mcid not in page_to_mcids[page]:
            page_to_mcids[page].append(mcid)

    value_sources = []
    for page in sorted(page_to_mcids):
        value_sources.append({
            'properties': [
                {
                    'property': 'xbrl:pdfPage',
                    'value': page
                },
                {
                    'property': 'xbrl:pdfMcid',
                    'value': page_to_mcids[page]
                }
            ]
        })

    return value_sources


def convert_fact_to_model10(fact_id, fact_obj, mcids):
    """Convert a single XBRL-JSON fact to Model 1.0 format.
    
    Returns:
        dict with Model 1.0 fact structure
    """
    value = fact_obj.get('value')
    decimals = fact_obj.get('decimals')
    dimensions = fact_obj.get('dimensions', {})
    
    # Build factDimensions
    fact_dimensions = {}
    for dim_key, dim_val in dimensions.items():
        # Convert dimension names to xbrl: prefix style if not already
        if ':' not in dim_key:
            fact_dimensions[f'xbrl:{dim_key}'] = dim_val
        else:
            fact_dimensions[dim_key] = dim_val
    
    model10_fact = {
        'name': f'converted:{fact_id}',
        'factDimensions': fact_dimensions,
    }
    
    # Try to find MCIDs for this fact
    mcid_matches = find_mcids_for_value(value, decimals, mcids)
    
    if mcid_matches:
        # Add valueSources in property-value form, nested in factValues.
        value_sources = build_value_sources(mcid_matches)

        if value_sources:
            model10_fact['factValues'] = [
                {
                    'name': f'converted:{fact_id}_val',
                    'valueSources': value_sources
                }
            ]
        else:
            model10_fact['factValues'] = [
                {'name': f'converted:{fact_id}_val', 'value': str(value)}
            ]
    else:
        # No MCID match; use direct value.
        model10_fact['factValues'] = [
            {'name': f'converted:{fact_id}_val', 'value': str(value)}
        ]
    
    return model10_fact


def convert_factset(source_json_path, mcid_file_path, output_json_path):
    """Convert XBRL-JSON factset to Model 1.0 format."""
    
    print(f"Loading source JSON: {source_json_path}")
    with open(source_json_path, 'r', encoding='utf-8') as f:
        source = json.load(f)
    
    print(f"Loading MCID mappings: {mcid_file_path}")
    mcids = load_mcids(mcid_file_path)
    print(f"  Loaded {len(mcids)} MCID entries")
    
    # Build Model 1.0 structure
    output = {
        'documentInfo': source.get('documentInfo', {}).copy(),
    }
    
    # Update documentType
    if 'documentInfo' in output:
        output['documentInfo']['documentType'] = 'https://xbrl.org/2026/model'
    
    # Convert facts
    source_facts = source.get('facts', {})
    converted_facts = []
    
    print(f"Converting {len(source_facts)} facts...")
    for fact_id, fact_obj in source_facts.items():
        model10_fact = convert_fact_to_model10(fact_id, fact_obj, mcids)
        converted_facts.append(model10_fact)
    
    # Wrap in xbrlModel
    output['xbrlModel'] = {
        'name': 'converted:loreal-2025-12-31',
        'facts': converted_facts,
    }
    
    # Write output
    print(f"Writing output: {output_json_path}")
    with open(output_json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"Done. Converted {len(converted_facts)} facts.")


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    
    source_json = sys.argv[1]
    mcid_file = sys.argv[2]
    output_json = sys.argv[3]
    
    convert_factset(source_json, mcid_file, output_json)
