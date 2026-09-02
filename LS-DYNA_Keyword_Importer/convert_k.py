# -*- coding: utf-8 -*-
"""Standalone conversion/diagnostic command (works outside Abaqus)."""
from __future__ import absolute_import, division, print_function

import argparse
import json
import sys

from lsk_converter import ConverterOptions, convert_keyword_file


try:
    _unicode_type = unicode
except NameError:
    _unicode_type = None


def _console_string(value):
    if _unicode_type is not None and isinstance(value, _unicode_type):
        try:
            encoding = getattr(getattr(sys, "stdout", None), "encoding", None)
        except Exception:
            encoding = None
        if not encoding:
            try:
                encoding = sys.getfilesystemencoding()
            except Exception:
                encoding = None
        encoding = encoding or "utf-8"
        return value.encode(encoding, "replace")
    return value


def main():
    parser = argparse.ArgumentParser(description="Convert LS-DYNA .k to Abaqus .inp")
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--ale", choices=("AUTO", "ADAPTIVE", "EULERIAN", "IGNORE"), default="AUTO")
    parser.add_argument("--contact", choices=("AUTO", "GENERAL"), default="AUTO")
    parser.add_argument("--stop-on-unsupported", action="store_true")
    parser.add_argument("--no-includes", action="store_true")
    parser.add_argument("--sale-max-elements", type=int, default=5000000)
    parser.add_argument("--shared-nodes", choices=("MPC", "EQUATION", "NONE"),
                        default="MPC")
    args = parser.parse_args()
    options = ConverterOptions(
        recursive_includes=not args.no_includes,
        ale_mode=args.ale,
        contact_mode=args.contact,
        unsupported_policy="STOP" if args.stop_on_unsupported else "REPORT",
        sale_max_elements=args.sale_max_elements,
        shared_node_mode=args.shared_nodes,
    )
    result = convert_keyword_file(args.source, args.output, options)
    print(json.dumps(result["statistics"], indent=2, sort_keys=True))
    print("Report: %s" % _console_string(result["html_report"]))


if __name__ == "__main__":
    main()
