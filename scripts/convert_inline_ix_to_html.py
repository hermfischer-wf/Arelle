#!/usr/bin/env python3
"""Convert an Inline XBRL XHTML file to plain HTML-like XHTML.

Transformations:
1. Remove elements hidden by display:none (inline style or CSS class rules).
2. Convert ix:* elements to span or div while preserving id and non-ix attributes.
3. Drop ix-specific attributes (e.g., contextRef, name, format, scale, unitRef, etc.).

Usage:
  python convert_inline_ix_to_html.py <input.xhtml> <output.xhtml>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
XML_NS = "http://www.w3.org/XML/1998/namespace"
XHTML_NS = "http://www.w3.org/1999/xhtml"

ET.register_namespace("", XHTML_NS)

DISPLAY_NONE_RE = re.compile(r"(?:^|;)\s*display\s*:\s*none\b", re.IGNORECASE)
CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
SIMPLE_CLASS_SELECTOR_RE = re.compile(r"^\.([A-Za-z_][\w\-]*)$")

BLOCK_IX_LOCAL_NAMES = {
    "header",
    "hidden",
    "references",
    "resources",
    "relationship",
    "continuation",
    "exclude",
    "footnote",
    "tuple",
}


def local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[1]
    return tag


def is_ix_tag(tag: str) -> bool:
    return tag.startswith("{" + IX_NS + "}")


def style_is_display_none(style: str | None) -> bool:
    if not style:
        return False
    return bool(DISPLAY_NONE_RE.search(style))


def extract_hidden_classes(root: ET.Element) -> set[str]:
    """Find CSS classes that define display:none in embedded <style> blocks."""
    hidden_classes: set[str] = set()

    for elem in root.iter():
        if local_name(elem.tag) != "style":
            continue
        css = elem.text or ""
        for selector_text, declarations in CSS_RULE_RE.findall(css):
            if not style_is_display_none(declarations):
                continue
            selectors = [s.strip() for s in selector_text.split(",")]
            for selector in selectors:
                m = SIMPLE_CLASS_SELECTOR_RE.match(selector)
                if m:
                    hidden_classes.add(m.group(1))

    return hidden_classes


def has_hidden_class(elem: ET.Element, hidden_classes: set[str]) -> bool:
    class_attr = elem.attrib.get("class", "")
    if not class_attr:
        return False
    elem_classes = class_attr.split()
    return any(c in hidden_classes for c in elem_classes)


def choose_replacement_tag(ix_local_name: str) -> str:
    if ix_local_name in BLOCK_IX_LOCAL_NAMES:
        return "div"
    return "span"


def convert_ix_element(elem: ET.Element) -> None:
    ix_local = local_name(elem.tag)
    elem.tag = choose_replacement_tag(ix_local)

    new_attrib: dict[str, str] = {}
    for attr_name, attr_value in elem.attrib.items():
        if attr_name.startswith("{" + IX_NS + "}"):
            continue
        # Drop prefixed ix attributes even when parser keeps lexical prefix.
        if attr_name.startswith("ix:"):
            continue
        # Keep id and non-ix attributes.
        new_attrib[attr_name] = attr_value

    elem.attrib.clear()
    elem.attrib.update(new_attrib)


def transform_tree(root: ET.Element) -> tuple[int, int]:
    removed_count = 0
    converted_count = 0
    hidden_classes = extract_hidden_classes(root)

    def recurse(parent: ET.Element) -> None:
        nonlocal removed_count, converted_count

        # Iterate over a copy since we may remove children.
        for child in list(parent):
            style = child.attrib.get("style")
            if style_is_display_none(style) or has_hidden_class(child, hidden_classes):
                parent.remove(child)
                removed_count += 1
                continue

            if is_ix_tag(child.tag):
                convert_ix_element(child)
                converted_count += 1

            recurse(child)

    if is_ix_tag(root.tag):
        convert_ix_element(root)
        converted_count += 1

    recurse(root)
    return removed_count, converted_count


def strip_whitespace_only_nodes(elem: ET.Element) -> None:
    # Whitespace-only text/tail nodes can change layout in this document because
    # many wrappers are relatively positioned rather than fully out-of-flow.
    if elem.text is not None and not elem.text.strip():
        elem.text = None
    if elem.tail is not None and not elem.tail.strip():
        elem.tail = None
    for child in elem:
        strip_whitespace_only_nodes(child)


def convert_file(input_path: Path, output_path: Path) -> tuple[int, int]:
    parser = ET.XMLParser()
    tree = ET.parse(str(input_path), parser=parser)
    root = tree.getroot()

    removed_count, converted_count = transform_tree(root)

    # Preserve XML declaration and write UTF-8 output without re-indenting,
    # which would introduce layout-affecting whitespace nodes.
    strip_whitespace_only_nodes(root)
    tree.write(
        str(output_path),
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=False,
    )

    return removed_count, converted_count


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        return 1

    removed_count, converted_count = convert_file(input_path, output_path)
    print(f"Wrote: {output_path}")
    print(f"Removed display:none elements: {removed_count}")
    print(f"Converted ix:* elements: {converted_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
