# -*- coding: utf-8 -*-
"""LS-DYNA keyword deck reader used by the Abaqus 2020 plug-in.

The module intentionally uses Python 2.7 compatible syntax because Abaqus 2020
ships with Python 2.7.  It can also be imported by Python 3 for unit tests.
"""
from __future__ import absolute_import, division, print_function

import ast
import io
import os
import re
import sys


try:
    text_type = unicode
    integer_type = long
except NameError:  # pragma: no cover - Python 3
    text_type = str
    integer_type = int


_IMPLICIT_EXP = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))([+-]\d+)$")
_PARAM_REF = re.compile(r"&([A-Za-z_][A-Za-z0-9_]*)")


class ParseError(Exception):
    pass


def path_text(value):
    """Return one Unicode filesystem-path type on Python 2 and Python 3.

    ``io.open`` returns Unicode keyword lines, while AFX file keywords can
    return Python 2 byte strings.  Mixing them in ``os.path.join`` breaks as
    soon as the project path contains non-ASCII characters.
    """
    if isinstance(value, text_type):
        return value
    if isinstance(value, bytes):
        encoding = sys.getfilesystemencoding() or "utf-8"
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            return value.decode("utf-8", "replace")
    return text_type(value)


class KeywordBlock(object):
    def __init__(self, name, options, lines, source, line_number):
        self.name = name.upper().strip()
        self.options = options
        self.lines = lines
        self.source = source
        self.line_number = line_number

    def location(self):
        return "%s:%s" % (self.source, self.line_number)

    def as_dict(self):
        return {
            "keyword": self.name,
            "options": self.options,
            "source": self.source,
            "line": self.line_number,
            "data_lines": len(self.lines),
        }


class Deck(object):
    def __init__(self, root_file):
        self.root_file = os.path.abspath(path_text(root_file))
        self.blocks = []
        self.files = []
        self.include_edges = []
        self.include_paths = []
        self.parameters = {}
        self.warnings = []

    def keyword_counts(self):
        counts = {}
        for block in self.blocks:
            counts[block.name] = counts.get(block.name, 0) + 1
        return counts


def strip_comment(line):
    """Remove LS-DYNA comments; a dollar sign starts a comment."""
    if "$" in line:
        line = line.split("$", 1)[0]
    return line.rstrip("\r\n")


def split_fields(line):
    """Split free format, comma format, or simple 10-column fixed format."""
    raw = strip_comment(line).strip()
    if not raw:
        return []
    if "," in raw:
        return [item.strip() for item in raw.split(",")]
    fields = raw.split()
    if len(fields) > 1:
        return fields
    # Some keyword exporters produce contiguous 10-character fields.
    if len(raw) > 10:
        chunks = [raw[i:i + 10].strip() for i in range(0, len(raw), 10)]
        if len([c for c in chunks if c]) > 1:
            return chunks
    return fields


def parse_float(value, default=0.0):
    if value is None:
        return default
    value = text_type(value).strip()
    if not value:
        return default
    value = value.replace("D", "E").replace("d", "e")
    match = _IMPLICIT_EXP.match(value)
    if match and "e" not in value.lower():
        value = match.group(1) + "E" + match.group(2)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_int(value, default=0):
    raw = text_type(value).strip()
    try:
        return integer_type(raw)
    except (TypeError, ValueError):
        try:
            return integer_type(float(raw))
        except (TypeError, ValueError, OverflowError):
            return default


class _SafeExpression(object):
    """Tiny arithmetic evaluator for *PARAMETER_EXPRESSION values."""
    _binary = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.Pow: lambda a, b: a ** b,
        ast.Mod: lambda a, b: a % b,
    }
    _unary = {ast.UAdd: lambda a: a, ast.USub: lambda a: -a}

    def evaluate(self, expression):
        node = ast.parse(expression, mode="eval")
        return self._walk(node.body)

    def _walk(self, node):
        if isinstance(node, ast.Num):
            return node.n
        if hasattr(ast, "Constant") and isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("non-numeric constant")
        if isinstance(node, ast.BinOp) and type(node.op) in self._binary:
            return self._binary[type(node.op)](self._walk(node.left), self._walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in self._unary:
            return self._unary[type(node.op)](self._walk(node.operand))
        raise ValueError("unsupported expression")


class DeckParser(object):
    def __init__(self, recursive=True, encoding="utf-8"):
        self.recursive = bool(recursive)
        self.encoding = encoding
        self.deck = None
        self._stack = []
        self._stack_keys = set()
        self._safe_eval = _SafeExpression()

    def parse(self, filename):
        root = os.path.abspath(path_text(filename))
        if not os.path.isfile(root):
            raise ParseError("LS-DYNA keyword file does not exist: %s" % root)
        self.deck = Deck(root)
        self._read_file(root, parent=None)
        return self.deck

    def _read_text(self, filename):
        encodings = [self.encoding, "utf-8-sig", "gb18030", "latin-1"]
        tried = []
        for encoding in encodings:
            if encoding in tried:
                continue
            tried.append(encoding)
            try:
                with io.open(filename, "r", encoding=encoding) as handle:
                    return handle.readlines()
            except UnicodeDecodeError:
                continue
        raise ParseError("Cannot decode keyword file: %s" % filename)

    def _read_file(self, filename, parent):
        filename = os.path.abspath(path_text(filename))
        normalized = os.path.normcase(filename)
        if normalized in self._stack_keys:
            chain = " -> ".join(self._stack + [filename])
            raise ParseError("Cyclic *INCLUDE detected: %s" % chain)
        if not os.path.isfile(filename):
            raise ParseError("Included file does not exist: %s" % filename)

        self._stack.append(filename)
        self._stack_keys.add(normalized)
        self.deck.files.append(filename)
        if parent:
            self.deck.include_edges.append((parent, filename))
        lines = self._read_text(filename)
        index = 0
        while index < len(lines):
            raw = strip_comment(lines[index]).strip()
            if not raw or not raw.startswith("*"):
                index += 1
                continue
            header_line = index + 1
            header = raw[1:].strip()
            pieces = [piece.strip() for piece in header.split(",")]
            name = pieces[0].upper()
            options = pieces[1:]
            index += 1
            data = []
            while index < len(lines):
                candidate = strip_comment(lines[index])
                if candidate.lstrip().startswith("*"):
                    break
                if candidate.strip():
                    data.append(candidate.rstrip())
                index += 1

            if name in ("KEYWORD", "END"):
                continue
            if name.startswith("INCLUDE_PATH"):
                self._handle_include_path(data, filename)
                continue
            if name.startswith("INCLUDE"):
                self._handle_include(data, filename, name, header_line)
                continue

            resolved = [self._substitute(line) for line in data]
            block = KeywordBlock(name, options, resolved, filename, header_line)
            self.deck.blocks.append(block)
            if name.startswith("PARAMETER"):
                self._capture_parameters(block)
        self._stack.pop()
        self._stack_keys.remove(normalized)

    def _handle_include_path(self, data, source):
        for line in data:
            path = self._substitute(strip_comment(line).strip().strip('"\''))
            if not path:
                continue
            if not os.path.isabs(path):
                path = os.path.join(os.path.dirname(source), path)
            path = os.path.abspath(path)
            if path not in self.deck.include_paths:
                self.deck.include_paths.append(path)

    def _handle_include(self, data, source, keyword, line_number):
        if not self.recursive:
            self.deck.warnings.append({
                "code": "INCLUDE_SKIPPED",
                "message": "Recursive include processing is disabled",
                "source": source,
                "line": line_number,
                "keyword": keyword,
            })
            return
        if "BINARY" in keyword or "TRANSFORM" in keyword:
            self.deck.warnings.append({
                "code": "INCLUDE_VARIANT_APPROX",
                "message": "%s is read as a plain include; binary data or transforms are not applied" % keyword,
                "source": source,
                "line": line_number,
                "keyword": keyword,
            })
        for raw_path in data:
            include_name = self._substitute(strip_comment(raw_path).strip().strip('"\''))
            if not include_name:
                continue
            include_file = self._resolve_include(include_name, source)
            if include_file is None:
                raise ParseError("Cannot resolve *INCLUDE '%s' referenced by %s:%s" %
                                 (include_name, source, line_number))
            self._read_file(include_file, parent=source)

    def _resolve_include(self, include_name, source):
        include_name = include_name.replace("\\", os.sep)
        candidates = []
        if os.path.isabs(include_name):
            candidates.append(include_name)
        else:
            candidates.append(os.path.join(os.path.dirname(source), include_name))
            for base in self.deck.include_paths:
                candidates.append(os.path.join(base, include_name))
            candidates.append(os.path.join(os.path.dirname(self.deck.root_file), include_name))
        for candidate in candidates:
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
        return None

    def _substitute(self, text):
        if "&" not in text:
            return text
        def replace(match):
            key = match.group(1).upper()
            return text_type(self.deck.parameters.get(key, match.group(0)))
        return _PARAM_REF.sub(replace, text)

    def _capture_parameters(self, block):
        for line in block.lines:
            fields = split_fields(line)
            if len(fields) < 2:
                continue
            # *PARAMETER permits several type/name/value triplets on one card.
            # *PARAMETER_EXPRESSION commonly uses a name/expression pair.
            triplets = []
            if fields[0].upper() in ("R", "I", "C"):
                cursor = 0
                while cursor + 2 < len(fields):
                    if fields[cursor].upper() not in ("R", "I", "C"):
                        break
                    triplets.append((fields[cursor].upper(), fields[cursor + 1],
                                     fields[cursor + 2]))
                    cursor += 3
            else:
                triplets.append(("R", fields[0], "".join(fields[1:])))
            for kind, raw_name, raw_value in triplets:
                name = raw_name.lstrip("&").upper()
                value_text = self._substitute(raw_value)
                if kind == "C":
                    value = value_text.strip('"\'')
                else:
                    try:
                        value = self._safe_eval.evaluate(value_text)
                        if kind == "I":
                            value = int(value)
                    except Exception:
                        value = parse_float(value_text, value_text)
                        self.deck.warnings.append({
                            "code": "PARAMETER_EXPRESSION_RAW",
                            "message": "Parameter %s could not be fully evaluated" % name,
                            "source": block.source,
                            "line": block.line_number,
                            "keyword": block.name,
                        })
                self.deck.parameters[name] = value
