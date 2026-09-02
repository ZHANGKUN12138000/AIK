# -*- coding: utf-8 -*-
"""LS-DYNA keyword to Abaqus/Explicit input-deck converter.

This is a conservative translator, not a solver-to-solver equivalence claim.
Every source block receives a report status.  Mappings that change the physical
formulation (notably ALE/CEL and tiebreak contact) are explicitly reported as
approximations.

Keep this module Python 2.7 compatible for Abaqus/CAE 2020.
"""
from __future__ import absolute_import, division, print_function

import collections
import datetime
import io
import json
import math
import os
import re

from lsk_parser import (DeckParser, ParseError, parse_float, parse_int,
                        path_text, split_fields)


try:
    text_type = unicode
    integer_types = (int, long)
except NameError:  # pragma: no cover
    text_type = str
    integer_types = (int,)


VERSION = "1.2.1"


# LS-DYNA solid formulations that represent an ALE/Eulerian domain.  ELFORM
# 11 is the common multi-material ALE formulation; 5, 6, 7 and 12 are kept
# here as the corresponding single-material/void formulations.
CEL_SOLID_ELFORMS = frozenset((5, 6, 7, 11, 12))


def _fmt(value):
    if isinstance(value, int):
        return text_type(value)
    try:
        return "%.12g" % float(value)
    except (TypeError, ValueError):
        return text_type(value)


def _name(prefix, value, title=None):
    seed = title if title else "%s_%s" % (prefix, value)
    seed = text_type(seed).strip()
    seed = re.sub(r"[^A-Za-z0-9_]", "_", seed)
    seed = re.sub(r"_+", "_", seed).strip("_")
    if not seed:
        seed = "%s_%s" % (prefix, value)
    if seed[0].isdigit():
        seed = "%s_%s" % (prefix, seed)
    return seed[:72]


def _chunks(values, size=16):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _title_and_rows(block):
    lines = list(block.lines)
    title = ""
    if "TITLE" in block.name and lines:
        title = lines.pop(0).strip()
    return title, _rows(lines)


def _rows(lines):
    """Split each non-empty card once (large decks used to split twice)."""
    result = []
    for line in lines:
        fields = split_fields(line)
        if fields:
            result.append(fields)
    return result


def _positive_ints(values):
    result = []
    for value in values:
        parsed = parse_int(value)
        if parsed > 0:
            result.append(parsed)
    return result


def _unit(vector):
    length = math.sqrt(sum(value * value for value in vector))
    if length <= 1.0e-30:
        raise ConversionError("Zero-length vector in SALE coordinate system")
    return tuple(value / length for value in vector)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


class ConversionError(Exception):
    pass


class ConverterOptions(object):
    def __init__(self, recursive_includes=True, ale_mode="AUTO",
                 contact_mode="AUTO", unsupported_policy="REPORT",
                 create_placeholder_materials=True, write_json=True,
                 write_html=True, sale_max_elements=5000000,
                 shared_node_mode="MPC"):
        self.recursive_includes = bool(recursive_includes)
        self.ale_mode = text_type(ale_mode).upper()
        self.contact_mode = text_type(contact_mode).upper()
        self.unsupported_policy = text_type(unsupported_policy).upper()
        self.create_placeholder_materials = bool(create_placeholder_materials)
        self.write_json = bool(write_json)
        self.write_html = bool(write_html)
        self.sale_max_elements = max(parse_int(sale_max_elements, 5000000), 1)
        self.shared_node_mode = text_type(shared_node_mode).upper()
        if self.shared_node_mode not in ("MPC", "EQUATION", "NONE"):
            raise ValueError("shared_node_mode must be MPC, EQUATION or NONE")


class ConversionReport(object):
    def __init__(self, source_file):
        self.source_file = source_file
        self.created_utc = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.records = []
        self.messages = []
        self.statistics = {}

    def record(self, block, status, target, note=""):
        self.records.append({
            "keyword": block.name,
            "source": block.source,
            "line": block.line_number,
            "status": status,
            "target": target,
            "note": note,
        })

    def message(self, severity, code, message, block=None):
        item = {"severity": severity, "code": code, "message": message}
        if block is not None:
            item.update({"keyword": block.name, "source": block.source,
                         "line": block.line_number})
        self.messages.append(item)

    def finalize(self, model, deck):
        statuses = collections.Counter([item["status"] for item in self.records])
        self.statistics = {
            "source_files": len(deck.files),
            "keyword_blocks": len(deck.blocks),
            "nodes": len(model.nodes),
            "elements": len(model.elements),
            "sale_meshes": len(model.sale_meshes),
            "parts": len(model.parts),
            "materials": len(model.materials),
            "equations_of_state": len(model.eos),
            "erosion_definitions": len(model.erosions),
            "hourglass_definitions": len(model.hourglasses),
            "node_sets": len(model.node_sets),
            "element_sets": len(model.element_sets),
            "segment_sets": len(model.segment_sets),
            "contacts": len(model.contacts),
            "loads_and_bcs": len(model.boundaries) + len(model.loads),
            "shared_node_labels": model.shared_node_labels,
            "shared_node_constraints": model.shared_node_constraints,
            "shared_node_skipped": model.shared_node_skipped,
            "converted": statuses.get("converted", 0),
            "approximated": statuses.get("approximated", 0),
            "ignored": statuses.get("ignored", 0),
            "unsupported": statuses.get("unsupported", 0),
            "warnings": len([m for m in self.messages if m["severity"] == "WARNING"]),
            "errors": len([m for m in self.messages if m["severity"] == "ERROR"]),
        }

    def as_dict(self, output_inp, options, deck):
        return {
            "converter": "LS-DYNA Keyword Importer for Abaqus/CAE 2020",
            "version": VERSION,
            "created_utc": self.created_utc,
            "source_file": self.source_file,
            "output_inp": output_inp,
            "options": {
                "recursive_includes": options.recursive_includes,
                "ale_mode": options.ale_mode,
                "contact_mode": options.contact_mode,
                "unsupported_policy": options.unsupported_policy,
                "create_placeholder_materials": options.create_placeholder_materials,
                "sale_max_elements": options.sale_max_elements,
                "shared_node_mode": options.shared_node_mode,
            },
            "source_files": deck.files,
            "include_edges": deck.include_edges,
            "parameters": deck.parameters,
            "statistics": self.statistics,
            "messages": self.messages,
            "keyword_records": self.records,
        }


class ModelData(object):
    def __init__(self):
        self.title = "Imported LS-DYNA model"
        self.nodes = collections.OrderedDict()
        self.elements = collections.OrderedDict()
        self.parts = collections.OrderedDict()
        self.sections = {}
        self.materials = {}
        self.eos = {}
        self.erosions = {}
        self.hourglasses = {}
        self.curves = {}
        self.node_sets = collections.defaultdict(list)
        self.element_sets = collections.defaultdict(list)
        self.part_sets = collections.defaultdict(list)
        self.segment_sets = collections.defaultdict(list)
        self.contacts = []
        self.boundaries = []
        self.loads = []
        self.initial_conditions = []
        self.initial_detonations = []
        self.rigidwalls = []
        self.termination_time = 1.0
        self.timestep = {}
        self.output = {
            "field_interval": None,
            "history_interval": None,
            "field": False,
            "nodal": False,
            "element": False,
            "energy": False,
            "contact": False,
            "history_nodes": [],
            "history_elements": [],
        }
        self.ale_keywords = []
        self.ale_part_ids = set()
        self.ale_mesh_part_ids = set()
        self.ale_material_part_ids = []
        self.sale_control_points = {}
        self.sale_mesh_specs = []
        self.sale_refines = {}
        self.sale_meshes = collections.OrderedDict()
        self.sale_set_specs = []
        self.sale_volume_fraction_blocks = []
        self.eulerian_volume_fractions = []
        self.coordinate_nodes = {}
        self.control_cards = []
        self.extra_comments = []
        self.shared_node_labels = 0
        self.shared_node_constraints = 0
        self.shared_node_skipped = 0

    def add_unique(self, mapping, key, values):
        current = mapping[key]
        seen = set(current)
        for value in values:
            if value not in seen:
                current.append(value)
                seen.add(value)


class LsdynaSemanticReader(object):
    def __init__(self, options, report, progress_callback=None):
        self.options = options
        self.report = report
        self.model = ModelData()
        self._handled = set()
        self.progress_callback = progress_callback

    def _progress(self, percent, message):
        if self.progress_callback is not None:
            self.progress_callback(percent, message)

    def read(self, deck):
        total = max(len(deck.blocks), 1)
        stride = max(total // 100, 1)
        for index, block in enumerate(deck.blocks):
            self._read_block(block)
            if index % stride == 0:
                self._progress(15 + int(20.0 * index / total),
                               "Converting keyword blocks")
        for warning in deck.warnings:
            self.report.messages.append(dict(warning, severity="WARNING"))
        self._postprocess()
        self._progress(55, "Semantic conversion completed")
        return self.model

    def _record(self, block, status, target, note=""):
        self.report.record(block, status, target, note)

    def _warn(self, block, code, message):
        self.report.message("WARNING", code, message, block)

    def _read_block(self, block):
        key = block.name.replace("-", "_")
        try:
            if key == "TITLE":
                if block.lines:
                    self.model.title = block.lines[0].strip()
                self._record(block, "converted", "*HEADING")
            elif key.startswith("NODE") and key in ("NODE", "NODE_MERGE"):
                self._nodes(block)
            elif key.startswith("ELEMENT_SOLID"):
                self._elements(block, "SOLID")
            elif key.startswith("ELEMENT_TSHELL"):
                self._elements(block, "TSHELL")
            elif key.startswith("ELEMENT_SHELL"):
                self._elements(block, "SHELL")
            elif key.startswith("ELEMENT_BEAM") or key.startswith("ELEMENT_SEATBELT"):
                self._elements(block, "BEAM")
            elif key.startswith("ELEMENT_SPH"):
                self._elements(block, "SPH")
            elif key in ("PART", "PART_TITLE"):
                self._part(block)
            elif key.startswith("SECTION_"):
                self._section(block)
            elif key.startswith("MAT_ADD_EROSION"):
                self._erosion(block)
            elif key.startswith("MAT_"):
                self._material(block)
            elif key.startswith("EOS_"):
                self._eos(block)
            elif key.startswith("DEFINE_COORDINATE_NODES"):
                self._coordinate_nodes(block)
            elif key.startswith("DEFINE_CURVE"):
                self._curve(block)
            elif key.startswith("SET_"):
                self._set(block)
            elif key.startswith("BOUNDARY_"):
                self._boundary(block)
            elif key.startswith("INITIAL_VOLUME_FRACTION_GEOMETRY"):
                self._sale_volume_fraction(block)
            elif key.startswith("INITIAL_"):
                self._initial(block)
            elif key.startswith("LOAD_"):
                self._load(block)
            elif key.startswith("CONTACT_"):
                self._contact(block)
            elif key.startswith("CONSTRAINED_LAGRANGE_IN_SOLID"):
                self._fsi_constraint(block)
            elif key.startswith("CONTROL_TERMINATION"):
                self._termination(block)
            elif key.startswith("CONTROL_TIMESTEP"):
                self._control_timestep(block)
            elif key.startswith("CONTROL_ALE") or key.startswith("ALE_") or key.startswith("SET_MULTI_MATERIAL_GROUP"):
                self._ale(block)
            elif key.startswith("DATABASE_"):
                self._database(block)
            elif key in ("KEYWORD_ID", "COMMENT", "ENDTIME"):
                self._record(block, "ignored", "report only", "Metadata/control marker")
            elif key.startswith("CONTROL_"):
                self.model.control_cards.append(block)
                self._record(block, "ignored", "Abaqus/Explicit defaults",
                             "Solver-specific control card retained in report")
            elif key.startswith("HOURGLASS"):
                self._hourglass(block)
            else:
                self._record(block, "unsupported", "none")
                self._warn(block, "UNSUPPORTED_KEYWORD", "%s has no active mapping" % key)
                if self.options.unsupported_policy == "STOP":
                    raise ConversionError("Unsupported keyword %s at %s" % (key, block.location()))
        except ConversionError:
            raise
        except Exception as exc:
            self._record(block, "unsupported", "none", "Parser error: %s" % exc)
            self.report.message("ERROR", "BLOCK_PARSE_ERROR", "%s" % exc, block)
            if self.options.unsupported_policy == "STOP":
                raise ConversionError("Cannot convert %s at %s: %s" %
                                      (key, block.location(), exc))

    def _nodes(self, block):
        count = 0
        for line in block.lines:
            fields = split_fields(line)
            if len(fields) < 4:
                continue
            nid = parse_int(fields[0])
            if nid <= 0:
                continue
            self.model.nodes[nid] = (parse_float(fields[1]), parse_float(fields[2]),
                                     parse_float(fields[3]))
            count += 1
        self._record(block, "converted", "*NODE", "%s nodes" % count)

    def _elements(self, block, family):
        count = 0
        for line in block.lines:
            fields = split_fields(line)
            if len(fields) < 4:
                continue
            eid = parse_int(fields[0])
            pid = parse_int(fields[1])
            if eid <= 0:
                continue
            if family in ("SOLID", "TSHELL"):
                raw_conn = []
                for value in fields[2:10]:
                    node = parse_int(value)
                    if node > 0:
                        raw_conn.append(node)
                conn = []
                for node in raw_conn:
                    if node not in conn:
                        conn.append(node)
            elif family == "SHELL":
                conn = []
                for value in fields[2:6]:
                    node = parse_int(value)
                    if node > 0:
                        conn.append(node)
                if len(conn) == 4 and conn[3] == conn[2]:
                    conn = conn[:3]
            elif family == "BEAM":
                conn = []
                for value in fields[2:4]:
                    node = parse_int(value)
                    if node > 0:
                        conn.append(node)
            else:  # SPH particle: eid, pid, node
                conn = [parse_int(fields[2])]
            if len(conn) < 1:
                continue
            self.model.elements[eid] = {"id": eid, "pid": pid, "family": family,
                                        "connectivity": tuple(conn)}
            # EIDs are unique by definition.  The previous add_unique call
            # rebuilt a growing set for every element and made this O(n^2).
            self.model.element_sets[pid].append(eid)
            count += 1
        target = {"SOLID": "C3D8R/C3D6/C3D5/C3D4", "TSHELL": "SC8R/C3D*",
                  "SHELL": "S4R/S3", "BEAM": "B31/T3D2", "SPH": "PC3D"}[family]
        status = "approximated" if family in ("TSHELL", "SPH") else "converted"
        self._record(block, status, "*ELEMENT, TYPE=%s" % target, "%s elements" % count)
        if family == "TSHELL":
            self._warn(block, "TSHELL_APPROX", "Thick shells are mapped by connectivity to continuum/shell elements")

    def _part(self, block):
        lines = list(block.lines)
        # A title line is mandatory on the classic *PART card even though the
        # keyword itself normally does not carry a _TITLE suffix.
        title = lines.pop(0).strip() if lines else ""
        rows = _rows(lines)
        if not rows:
            self._record(block, "unsupported", "none", "Empty *PART")
            return
        row = rows[0]
        pid = parse_int(row[0])
        if pid <= 0:
            self._record(block, "unsupported", "none", "Invalid part ID")
            return
        self.model.parts[pid] = {
            "pid": pid,
            "title": title or "LS-DYNA part %s" % pid,
            "name": _name("PART", pid, title),
            "section_id": parse_int(row[1]) if len(row) > 1 else 0,
            "material_id": parse_int(row[2]) if len(row) > 2 else 0,
            "eos_id": parse_int(row[3]) if len(row) > 3 else 0,
            "hourglass_id": parse_int(row[4]) if len(row) > 4 else 0,
        }
        self._record(block, "converted", "Abaqus ELSET/section assignment", "PID=%s" % pid)

    def _section(self, block):
        title, rows = _title_and_rows(block)
        if not rows:
            self._record(block, "unsupported", "none", "Empty section")
            return
        row = rows[0]
        sid = parse_int(row[0])
        kind = block.name.replace("SECTION_", "").replace("_TITLE", "")
        data = {"id": sid, "kind": kind, "title": title, "rows": rows}
        if kind.startswith("SOLID"):
            data["elform"] = parse_int(row[1]) if len(row) > 1 else 1
        elif kind.startswith("SHELL"):
            thicknesses = []
            if len(rows) > 1:
                thicknesses = [parse_float(v) for v in rows[1][:4] if text_type(v).strip()]
            data["thickness"] = sum(thicknesses) / len(thicknesses) if thicknesses else 1.0
        elif kind.startswith("BEAM"):
            data["area"] = parse_float(row[2], 1.0) if len(row) > 2 else 1.0
        self.model.sections[sid] = data
        target = "*SOLID SECTION"
        if kind.startswith("SHELL"):
            target = "*SHELL SECTION"
        elif kind.startswith("BEAM"):
            target = "*BEAM GENERAL SECTION"
        self._record(block, "converted", target, "SID=%s" % sid)

    def _material(self, block):
        title, rows = _title_and_rows(block)
        if not rows:
            self._record(block, "unsupported", "none", "Empty material")
            return
        mid = parse_int(rows[0][0])
        kind = block.name.replace("MAT_", "").replace("_TITLE", "")
        material = {"id": mid, "kind": kind, "title": title, "rows": rows}
        self.model.materials[mid] = material
        exact = ("ELASTIC", "001", "PLASTIC_KINEMATIC", "003",
                 "PIECEWISE_LINEAR_PLASTICITY", "024", "JOHNSON_COOK", "015",
                 "NULL", "009", "RIGID", "020", "HIGH_EXPLOSIVE_BURN", "008",
                 "ISOTROPIC_ELASTIC_PLASTIC", "012", "POWER_LAW_PLASTICITY", "018",
                 "MOONEY_RIVLIN_RUBBER", "027")
        status = "converted" if kind in exact else "approximated"
        self._record(block, status, "*MATERIAL", "MID=%s; model=%s" % (mid, kind))
        if status == "approximated":
            self._warn(block, "MATERIAL_APPROX", "%s uses an elastic fallback unless a dedicated mapping exists" % kind)

    def _eos(self, block):
        title, rows = _title_and_rows(block)
        if not rows:
            self._record(block, "unsupported", "none", "Empty EOS")
            return
        eosid = parse_int(rows[0][0])
        kind = block.name.replace("EOS_", "").replace("_TITLE", "")
        self.model.eos[eosid] = {"id": eosid, "kind": kind, "title": title, "rows": rows}
        known = kind in ("JWL", "002", "IDEAL_GAS", "LINEAR_POLYNOMIAL",
                         "GRUNEISEN", "004")
        exact = kind in ("JWL", "002")
        self._record(block, "converted" if exact else ("approximated" if known else "unsupported"),
                     "*EOS" if known else "none", "EOSID=%s; model=%s" % (eosid, kind))
        if not exact:
            self._warn(block, "EOS_REVIEW", "EOS conversion requires unit-system and parameter review")

    def _erosion(self, block):
        title, rows = _title_and_rows(block)
        if not rows or not rows[0]:
            self._record(block, "unsupported", "none", "Empty *MAT_ADD_EROSION")
            return
        mid = parse_int(rows[0][0])
        if mid <= 0:
            self._record(block, "unsupported", "none", "Invalid erosion material ID")
            return
        self.model.erosions[mid] = {
            "material_id": mid, "title": title, "rows": rows,
        }
        self._record(block, "approximated", "*SHEAR FAILURE/*TENSILE FAILURE",
                     "MID=%s; supported scalar criteria are mapped" % mid)
        self._warn(block, "EROSION_REVIEW",
                   "MAT_ADD_EROSION criteria without a direct Abaqus equivalent remain listed in the report")

    def _hourglass(self, block):
        title, rows = _title_and_rows(block)
        if not rows or not rows[0]:
            self._record(block, "unsupported", "none", "Empty *HOURGLASS")
            return
        count = 0
        for row in rows:
            hgid = parse_int(row[0]) if row else 0
            if hgid <= 0:
                continue
            self.model.hourglasses[hgid] = {
                "id": hgid, "title": title,
                "ihq": parse_int(row[1]) if len(row) > 1 else 0,
                "qm": parse_float(row[2], 0.1) if len(row) > 2 else 0.1,
                "q1": parse_float(row[4], 1.5) if len(row) > 4 else 1.5,
                "q2": parse_float(row[5], 0.06) if len(row) > 5 else 0.06,
                "rows": [row],
            }
            count += 1
        self._record(block, "approximated", "*SECTION CONTROLS",
                     "%s hourglass definitions" % count)
        self._warn(block, "HOURGLASS_SCALED",
                   "LS-DYNA hourglass coefficients are normalized to Abaqus section-control scale factors")

    def _coordinate_nodes(self, block):
        rows = _rows(block.lines)
        if not rows or len(rows[0]) < 4:
            self._record(block, "unsupported", "none", "Invalid coordinate-system card")
            return
        row = rows[0]
        lcsid = parse_int(row[0])
        self.model.coordinate_nodes[lcsid] = {
            "id": lcsid,
            "nodes": (parse_int(row[1]), parse_int(row[2]), parse_int(row[3])),
            "moving": parse_int(row[4]) if len(row) > 4 else 0,
        }
        self._record(block, "approximated", "SALE mesh orientation", "LCSID=%s" % lcsid)
        if self.model.coordinate_nodes[lcsid]["moving"]:
            self._warn(block, "SALE_ROTATING_FRAME_REVIEW",
                       "Moving/rotating SALE coordinate systems are imported at their initial orientation only")

    def _curve(self, block):
        title, rows = _title_and_rows(block)
        if not rows:
            self._record(block, "unsupported", "none", "Empty curve")
            return
        header = rows[0]
        lcid = parse_int(header[0])
        sfa = parse_float(header[1], 1.0) if len(header) > 1 else 1.0
        sfo = parse_float(header[2], 1.0) if len(header) > 2 else 1.0
        offa = parse_float(header[3], 0.0) if len(header) > 3 else 0.0
        offo = parse_float(header[4], 0.0) if len(header) > 4 else 0.0
        points = []
        for row in rows[1:]:
            if len(row) < 2:
                continue
            points.append((parse_float(row[0]) * sfa + offa,
                           parse_float(row[1]) * sfo + offo))
        points.sort(key=lambda pair: pair[0])
        self.model.curves[lcid] = {"id": lcid, "title": title, "points": points}
        self._record(block, "converted", "*AMPLITUDE", "LCID=%s; %s points" % (lcid, len(points)))

    def _set(self, block):
        title, rows = _title_and_rows(block)
        if not rows:
            self._record(block, "unsupported", "none", "Empty set")
            return
        name = block.name.replace("-", "_").replace("_TITLE", "")
        is_add = name.endswith("_ADD")
        name = name.replace("_ADD", "")
        sid = parse_int(rows[0][0])
        data_rows = rows[1:]
        if name.startswith(("SET_NODE_GENERAL", "SET_SOLID_GENERAL",
                            "SET_SEGMENT_GENERAL")):
            entity = "NODE" if name.startswith("SET_NODE") else (
                "SOLID" if name.startswith("SET_SOLID") else "SEGMENT")
            accepted = 0
            for row in data_rows:
                option = text_type(row[0]).upper() if row else ""
                if option not in ("SALEFAC", "SALECPT"):
                    continue
                self.model.sale_set_specs.append({
                    "sid": sid, "entity": entity, "option": option,
                    "values": list(row[1:]), "block": block,
                })
                accepted += 1
            if accepted:
                target = {"NODE": "*NSET", "SOLID": "*ELSET",
                          "SEGMENT": "*SURFACE"}[entity]
                self._record(block, "converted", target,
                             "SALE %s set SID=%s; %s selectors" % (entity, sid, accepted))
            else:
                self._record(block, "unsupported", "none",
                             "Only SALEFAC/SALECPT general-set selectors are mapped")
                self._warn(block, "SET_GENERAL_UNSUPPORTED",
                           "%s has no SALEFAC or SALECPT selector" % block.name)
            return
        if "GENERATE" in name:
            values = []
            for row in data_rows:
                if len(row) < 2:
                    continue
                start = parse_int(row[0])
                end = parse_int(row[1])
                step = parse_int(row[2], 1) if len(row) > 2 else 1
                if step == 0:
                    step = 1
                values.extend(list(range(start, end + (1 if step > 0 else -1), step)))
        else:
            values = []
            for row in data_rows:
                for value in row:
                    parsed = parse_int(value)
                    if parsed > 0:
                        values.append(parsed)

        if name.startswith("SET_NODE"):
            self.model.add_unique(self.model.node_sets, sid, values)
            target = "*NSET"
        elif name.startswith(("SET_SOLID", "SET_SHELL", "SET_BEAM", "SET_DISCRETE")):
            self.model.add_unique(self.model.element_sets, "SET_%s" % sid, values)
            target = "*ELSET"
        elif name.startswith("SET_PART"):
            self.model.add_unique(self.model.part_sets, sid, values)
            target = "*ELSET (expanded part IDs)"
        elif name.startswith("SET_SEGMENT"):
            segments = []
            for row in data_rows:
                nodes = []
                for value in row[:4]:
                    node = parse_int(value)
                    if node > 0:
                        nodes.append(node)
                nodes = tuple(nodes)
                if len(nodes) >= 3:
                    segments.append(nodes)
            current = self.model.segment_sets[sid]
            for segment in segments:
                if segment not in current:
                    current.append(segment)
            target = "*SURFACE, TYPE=ELEMENT"
            values = segments
        elif name.startswith("SET_MULTI_MATERIAL_GROUP"):
            self.model.ale_part_ids.update(values)
            target = "ALE domain ELSET"
        else:
            self._record(block, "unsupported", "none")
            self._warn(block, "SET_TYPE_UNSUPPORTED", "%s is not yet mapped" % block.name)
            return
        self._record(block, "converted", target, "SID=%s; %s entries" % (sid, len(values)))
        if is_add:
            self._warn(block, "SET_ADD_MERGED", "Set addition was merged into the target set")

    def _boundary(self, block):
        title, rows = _title_and_rows(block)
        kind = block.name.replace("_TITLE", "")
        converted = 0
        approximate = False
        for row in rows:
            if not row:
                continue
            if kind.startswith("BOUNDARY_SPC_NODE"):
                if len(row) < 3:
                    continue
                target = parse_int(row[0])
                cid = parse_int(row[1])
                flags = [parse_int(v) for v in row[2:8]]
                self.model.boundaries.append({"kind": "SPC_NODE", "target": target,
                                              "cid": cid, "flags": flags})
            elif kind.startswith("BOUNDARY_SPC_SET"):
                if len(row) < 3:
                    continue
                target = parse_int(row[0])
                cid = parse_int(row[1])
                flags = [parse_int(v) for v in row[2:8]]
                self.model.boundaries.append({"kind": "SPC_SET", "target": target,
                                              "cid": cid, "flags": flags})
            elif "PRESCRIBED_MOTION" in kind:
                if len(row) < 4:
                    continue
                target = parse_int(row[0])
                dof = parse_int(row[1])
                vad = parse_int(row[2])
                lcid = parse_int(row[3])
                scale = parse_float(row[4], 1.0) if len(row) > 4 else 1.0
                entity = "SET" if "SET" in kind else "NODE"
                self.model.boundaries.append({"kind": "MOTION_" + entity,
                                              "target": target, "dof": dof,
                                              "vad": vad, "curve": lcid, "scale": scale})
                if len(row) > 6 and any(abs(parse_float(v)) > 0.0 for v in row[6:8]):
                    approximate = True
            else:
                self._record(block, "unsupported", "none")
                self._warn(block, "BOUNDARY_UNSUPPORTED", "%s is not mapped" % block.name)
                return
            converted += 1
        self._record(block, "approximated" if approximate else "converted",
                     "*BOUNDARY", "%s records" % converted)
        if approximate:
            self._warn(block, "MOTION_BIRTH_DEATH_REVIEW",
                       "Prescribed-motion birth/death times are not reproduced automatically")
        if any(item.get("cid", 0) for item in self.model.boundaries[-converted:]):
            self._warn(block, "LOCAL_CSYS_REVIEW", "Nonzero LS-DYNA coordinate IDs require manual orientation review")

    def _sale_volume_fraction(self, block):
        self.model.sale_volume_fraction_blocks.append(block)
        if self.options.ale_mode in ("AUTO", "EULERIAN"):
            self._record(block, "approximated",
                         "*INITIAL CONDITIONS, TYPE=VOLUME FRACTION",
                         "SALE geometry is sampled at generated-cell centroids")
            self._warn(block, "SALE_VOLUME_FRACTION_CENTROID",
                       "SALE material boundaries are approximated by whole-cell centroid filling")
        elif self.options.ale_mode == "IGNORE":
            self._record(block, "ignored", "none", "ALE conversion disabled")
        else:
            self._record(block, "approximated", "conversion report",
                         "Volume fractions require EULERIAN mode")
            self._warn(block, "SALE_VOLUME_FRACTION_REQUIRES_CEL",
                       "Use EULERIAN mode to emit Abaqus material volume fractions")

    def _initial(self, block):
        title, rows = _title_and_rows(block)
        kind = block.name.replace("_TITLE", "")
        count = 0
        if kind.startswith("INITIAL_DETONATION"):
            for row in rows:
                if len(row) < 3:
                    continue
                self.model.initial_detonations.append({
                    "coordinates": (parse_float(row[0]), parse_float(row[1]),
                                    parse_float(row[2])),
                    "delay": parse_float(row[3], 0.0) if len(row) > 3 else 0.0,
                    "part_id": parse_int(row[4]) if len(row) > 4 else 0,
                })
                count += 1
            self._record(block, "converted", "*DETONATION POINT",
                         "%s detonation points" % count)
            return
        if kind.startswith("INITIAL_VELOCITY_NODE"):
            for row in rows:
                if len(row) < 4:
                    continue
                self.model.initial_conditions.append({
                    "kind": "NODE_VELOCITY", "target": parse_int(row[0]),
                    "values": tuple([parse_float(v) for v in row[1:7]]),
                })
                count += 1
        elif kind.startswith("INITIAL_VELOCITY_GENERATION"):
            for row in rows:
                if len(row) < 4:
                    continue
                self.model.initial_conditions.append({
                    "kind": "PART_VELOCITY", "target": parse_int(row[0]),
                    "values": tuple([parse_float(v) for v in row[1:7]]),
                })
                count += 1
        else:
            self._record(block, "unsupported", "none")
            self._warn(block, "INITIAL_UNSUPPORTED", "%s is not mapped" % block.name)
            return
        status = "approximated" if kind.startswith("INITIAL_VELOCITY_GENERATION") else "converted"
        self._record(block, status, "*INITIAL CONDITIONS, TYPE=VELOCITY", "%s records" % count)

    def _load(self, block):
        title, rows = _title_and_rows(block)
        kind = block.name.replace("_TITLE", "")
        count = 0
        approximate = False
        for row in rows:
            if not row:
                continue
            if kind.startswith("LOAD_NODE_POINT"):
                if len(row) < 3:
                    continue
                self.model.loads.append({"kind": "CLOAD_NODE", "target": parse_int(row[0]),
                                         "dof": parse_int(row[1]), "curve": parse_int(row[2]),
                                         "scale": parse_float(row[3], 1.0) if len(row) > 3 else 1.0})
            elif kind.startswith("LOAD_NODE_SET"):
                if len(row) < 3:
                    continue
                self.model.loads.append({"kind": "CLOAD_SET", "target": parse_int(row[0]),
                                         "dof": parse_int(row[1]), "curve": parse_int(row[2]),
                                         "scale": parse_float(row[3], 1.0) if len(row) > 3 else 1.0})
            elif kind.startswith("LOAD_BODY_"):
                axis = kind[-1]
                self.model.loads.append({"kind": "BODY", "axis": axis,
                                         "curve": parse_int(row[0]),
                                         "scale": parse_float(row[1], 1.0) if len(row) > 1 else 1.0})
                approximate = True
            elif kind.startswith("LOAD_SEGMENT"):
                if len(row) < 6:
                    continue
                self.model.loads.append({"kind": "SEGMENT_PRESSURE",
                                         "curve": parse_int(row[0]),
                                         "scale": parse_float(row[1], 1.0),
                                         "nodes": tuple(_positive_ints(row[2:6]))})
                approximate = True
            else:
                self._record(block, "unsupported", "none")
                self._warn(block, "LOAD_UNSUPPORTED", "%s is not mapped" % block.name)
                return
            count += 1
        self._record(block, "approximated" if approximate else "converted",
                     "*CLOAD/*DLOAD", "%s records" % count)

    def _contact(self, block):
        title, rows = _title_and_rows(block)
        kind = block.name.replace("_TITLE", "")
        if not rows:
            self._record(block, "unsupported", "none", "Empty contact")
            return
        card1 = rows[0]
        card2 = rows[1] if len(rows) > 1 else []
        contact = {
            "id": len(self.model.contacts) + 1,
            "kind": kind,
            "title": title,
            "ssid": parse_int(card1[0]) if len(card1) > 0 else 0,
            "msid": parse_int(card1[1]) if len(card1) > 1 else 0,
            "sstyp": parse_int(card1[2]) if len(card1) > 2 else 0,
            "mstyp": parse_int(card1[3]) if len(card1) > 3 else 0,
            "fs": parse_float(card2[0], 0.0) if len(card2) > 0 else 0.0,
            "fd": parse_float(card2[1], 0.0) if len(card2) > 1 else 0.0,
        }
        self.model.contacts.append(contact)
        tied = "TIED" in kind
        tiebreak = "TIEBREAK" in kind
        single = "SINGLE_SURFACE" in kind or "GENERAL" in kind
        if tiebreak:
            self._record(block, "approximated", "*TIE", "Failure criterion is not preserved")
            self._warn(block, "TIEBREAK_TO_TIE", "Tiebreak contact is imported as a permanent Abaqus tie")
        elif tied:
            self._record(block, "converted", "*TIE")
        elif single:
            self._record(block, "approximated", "Abaqus general contact")
        else:
            self._record(block, "converted", "*CONTACT PAIR/Abaqus general contact")

    def _fsi_constraint(self, block):
        title, rows = _title_and_rows(block)
        row = rows[0] if rows else []
        self.model.contacts.append({
            "id": len(self.model.contacts) + 1,
            "kind": "GENERAL_FSI_APPROX",
            "title": title,
            "ssid": parse_int(row[0]) if len(row) > 0 else 0,
            "msid": parse_int(row[1]) if len(row) > 1 else 0,
            "sstyp": 2,
            "mstyp": 2,
            "fs": 0.0,
            "fd": 0.0,
        })
        self._record(block, "approximated", "Abaqus general contact / CEL coupling review")
        self._warn(block, "FSI_COUPLING_REVIEW",
                   "Constrained Lagrange-in-solid coupling is represented by general contact intent; reconstruct CEL coupling manually")

    def _termination(self, block):
        rows = _rows(block.lines)
        if rows and rows[0]:
            self.model.termination_time = max(parse_float(rows[0][0], 1.0), 1.0e-15)
        self._record(block, "converted", "*DYNAMIC, EXPLICIT", "End time=%s" % self.model.termination_time)

    def _control_timestep(self, block):
        rows = _rows(block.lines)
        if rows:
            row = rows[0]
            self.model.timestep = {
                "dtinit": parse_float(row[0], 0.0) if len(row) > 0 else 0.0,
                "tssfac": parse_float(row[1], 0.9) if len(row) > 1 else 0.9,
                "isdo": parse_int(row[2]) if len(row) > 2 else 0,
                "dt2ms": parse_float(row[4], 0.0) if len(row) > 4 else 0.0,
            }
        self._record(block, "approximated", "Abaqus/Explicit time incrementation",
                     "Stable increment is controlled by Abaqus; mass scaling is reported")
        self._warn(block, "TIMESTEP_REVIEW", "LS-DYNA timestep controls are not solver-equivalent")

    def _ale(self, block):
        self.model.ale_keywords.append(block)
        key = block.name.replace("-", "_")
        unused_title, rows = _title_and_rows(block)
        if key.startswith("ALE_STRUCTURED_MESH_CONTROL_POINTS"):
            if not rows:
                self._record(block, "unsupported", "none", "Empty SALE control-point card")
                return
            header = rows[0]
            cpid = parse_int(header[0])
            scale = parse_float(header[1], 1.0) if len(header) > 1 else 1.0
            offset = parse_float(header[2], 0.0) if len(header) > 2 else 0.0
            points = []
            for row in rows[1:]:
                if len(row) >= 2:
                    points.append((parse_int(row[0]), parse_float(row[1]) * scale + offset))
            points.sort(key=lambda item: item[0])
            self.model.sale_control_points[cpid] = points
            self._record(block, "converted", "SALE structured-grid axis",
                         "CPID=%s; %s control points" % (cpid, len(points)))
            return
        if key.startswith("ALE_STRUCTURED_MESH_REFINE"):
            for row in rows:
                if len(row) >= 4:
                    mshid = parse_int(row[0])
                    self.model.sale_refines[mshid] = (
                        max(parse_int(row[1], 1), 1),
                        max(parse_int(row[2], 1), 1),
                        max(parse_int(row[3], 1), 1))
            self._record(block, "converted", "Refined generated SALE mesh")
            return
        if key == "ALE_STRUCTURED_MESH" or key.startswith("ALE_STRUCTURED_MESH_TITLE"):
            if len(rows) < 2:
                self._record(block, "unsupported", "none", "SALE mesh requires two cards")
                return
            card1, card2 = rows[0], rows[1]
            spec = {
                "mshid": parse_int(card1[0]),
                "pid": parse_int(card1[1]) if len(card1) > 1 else 0,
                "nbid": parse_int(card1[2]) if len(card1) > 2 else 0,
                "ebid": parse_int(card1[3]) if len(card1) > 3 else 0,
                "cpids": (parse_int(card2[0]), parse_int(card2[1]), parse_int(card2[2])),
                "nid0": parse_int(card2[3]) if len(card2) > 3 else 0,
                "lcsid": parse_int(card2[4]) if len(card2) > 4 else 0,
                "block": block,
            }
            self.model.sale_mesh_specs.append(spec)
            self.model.ale_mesh_part_ids.add(spec["pid"])
            self._record(block, "converted", "Generated EC3D8R/C3D8R mesh",
                         "MSHID=%s; PID=%s" % (spec["mshid"], spec["pid"]))
            return
        if key.startswith("ALE_MULTI_MATERIAL_GROUP"):
            for row in rows:
                if not row:
                    continue
                pid = parse_int(row[0])
                if pid > 0:
                    self.model.ale_part_ids.add(pid)
                    if pid not in self.model.ale_material_part_ids:
                        self.model.ale_material_part_ids.append(pid)
            target = ("Abaqus Eulerian material list"
                      if self.options.ale_mode in ("AUTO", "EULERIAN")
                      else "ALE domain metadata")
            self._record(block, "approximated", target)
            return
        if key.startswith("ALE_STRUCTURED_FSI"):
            self.model.contacts.append({
                "id": len(self.model.contacts) + 1,
                "kind": "GENERAL_SALE_FSI_APPROX", "title": "SALE FSI",
                "ssid": 0, "msid": 0, "sstyp": 5, "mstyp": 5,
                "fs": 0.0, "fd": 0.0,
            })
            self._record(block, "approximated", "Abaqus CEL general contact")
            self._warn(block, "SALE_FSI_REVIEW",
                       "SALE structured FSI is represented by CEL all-exterior general contact")
            return
        if key.startswith(("ALE_STRUCTURED_MESH_MOTION", "ALE_ESSENTIAL_BOUNDARY")):
            self._record(block, "approximated", "Initial mesh/boundary state")
            self._warn(block, "SALE_DYNAMIC_MESH_REVIEW",
                       "%s requires manual reconstruction in Abaqus" % key)
            return
        if self.options.ale_mode == "IGNORE":
            self._record(block, "ignored", "none", "ALE conversion disabled")
        elif self.options.ale_mode in ("AUTO", "EULERIAN"):
            self._record(block, "approximated", "Abaqus CEL Eulerian domain")
            self._warn(block, "ALE_TO_CEL_REVIEW", "ALE/S-ALE solver controls are not solver-equivalent")
        else:
            self._record(block, "approximated", "*ADAPTIVE MESH")
            self._warn(block, "ALE_FORMULATION_CHANGE", "LS-DYNA ALE is mapped to Abaqus ALE adaptive meshing; this is not identical to multi-material ALE")

    def _database(self, block):
        key = block.name
        rows = _rows(block.lines)
        dt = parse_float(rows[0][0], 0.0) if rows and rows[0] else 0.0
        if key.startswith("DATABASE_BINARY_D3PLOT"):
            self.model.output["field"] = True
            self.model.output["nodal"] = True
            self.model.output["element"] = True
            if dt > 0:
                self.model.output["field_interval"] = dt
            target = "*OUTPUT, FIELD"
        elif key.startswith(("DATABASE_BINARY_D3THDT", "DATABASE_GLSTAT", "DATABASE_MATSUM")):
            self.model.output["energy"] = True
            if dt > 0:
                self.model.output["history_interval"] = dt
            target = "*OUTPUT, HISTORY / *ENERGY OUTPUT"
        elif key.startswith("DATABASE_NODOUT"):
            self.model.output["nodal"] = True
            if dt > 0:
                self.model.output["history_interval"] = dt
            target = "*NODE OUTPUT"
        elif key.startswith(("DATABASE_ELOUT", "DATABASE_SLEOUT")):
            self.model.output["element"] = True
            if dt > 0:
                self.model.output["history_interval"] = dt
            target = "*ELEMENT OUTPUT"
        elif key.startswith(("DATABASE_RCFORC", "DATABASE_NCFORC", "DATABASE_BINARY_INTFOR")):
            self.model.output["contact"] = True
            if dt > 0:
                self.model.output["history_interval"] = dt
            target = "*CONTACT OUTPUT"
        elif key.startswith("DATABASE_HISTORY_NODE"):
            for row in rows:
                self.model.output["history_nodes"].extend(_positive_ints(row))
            target = "history *NODE OUTPUT"
        elif key.startswith(("DATABASE_HISTORY_SOLID", "DATABASE_HISTORY_SHELL")):
            for row in rows:
                self.model.output["history_elements"].extend(_positive_ints(row))
            target = "history *ELEMENT OUTPUT"
        elif key.startswith("DATABASE_EXTENT") or key.startswith("DATABASE_FORMAT"):
            self._record(block, "ignored", "Abaqus ODB settings", "LS-DYNA binary format details are not applicable")
            return
        else:
            self._record(block, "unsupported", "none")
            self._warn(block, "DATABASE_UNSUPPORTED", "%s output is not mapped" % key)
            return
        self._record(block, "converted", target)

    def _expand_sale_axis(self, cpid, refine, block):
        points = self.model.sale_control_points.get(cpid, [])
        if len(points) < 2:
            raise ConversionError("SALE CPID %s needs at least two control points at %s" %
                                  (cpid, block.location()))
        base = []
        start_index = points[0][0]
        for point_index in range(len(points) - 1):
            index0, value0 = points[point_index]
            index1, value1 = points[point_index + 1]
            span = index1 - index0
            if span <= 0:
                raise ConversionError("SALE CPID %s indices must increase" % cpid)
            for offset in range(span):
                ratio = offset / float(span)
                base.append(value0 + ratio * (value1 - value0))
        base.append(points[-1][1])
        refined = []
        refine = max(parse_int(refine, 1), 1)
        for index in range(len(base) - 1):
            for offset in range(refine):
                ratio = offset / float(refine)
                refined.append(base[index] + ratio * (base[index + 1] - base[index]))
        refined.append(base[-1])
        return refined, start_index, points[-1][0]

    def _sale_orientation(self, spec):
        origin = self.model.nodes.get(spec.get("nid0", 0), (0.0, 0.0, 0.0))
        lcsid = spec.get("lcsid", 0)
        if not lcsid:
            return origin, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                            (0.0, 0.0, 1.0))
        coordinate = self.model.coordinate_nodes.get(lcsid)
        if coordinate is None:
            self.report.message("WARNING", "SALE_COORDINATE_MISSING",
                                "SALE LCSID %s is undefined; global axes are used" % lcsid,
                                spec.get("block"))
            return origin, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                            (0.0, 0.0, 1.0))
        nid1, nid2, nid3 = coordinate["nodes"]
        if nid1 not in self.model.nodes or nid2 not in self.model.nodes or nid3 not in self.model.nodes:
            self.report.message("WARNING", "SALE_COORDINATE_NODE_MISSING",
                                "SALE LCSID %s references undefined orientation nodes; global axes are used" % lcsid,
                                spec.get("block"))
            return origin, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                            (0.0, 0.0, 1.0))
        p1 = self.model.nodes[nid1]
        p2 = self.model.nodes[nid2]
        p3 = self.model.nodes[nid3]
        ex = _unit(tuple(p2[i] - p1[i] for i in range(3)))
        trial = tuple(p3[i] - p1[i] for i in range(3))
        ez = _unit(_cross(ex, trial))
        ey = _unit(_cross(ez, ex))
        return origin, (ex, ey, ez)

    def _sale_xyz(self, meta, xvalue, yvalue, zvalue):
        origin = meta["origin"]
        ex, ey, ez = meta["basis"]
        return tuple(origin[index] + xvalue * ex[index] +
                     yvalue * ey[index] + zvalue * ez[index]
                     for index in range(3))

    def _sale_nid(self, meta, i, j, k):
        return meta["nbid"] + (k * meta["ny"] + j) * meta["nx"] + i

    def _sale_eid(self, meta, i, j, k):
        return meta["ebid"] + (k * (meta["ny"] - 1) + j) * (meta["nx"] - 1) + i

    def _generate_sale_meshes(self):
        mesh_count = max(len(self.model.sale_mesh_specs), 1)
        for mesh_index, spec in enumerate(self.model.sale_mesh_specs):
            refine = self.model.sale_refines.get(spec["mshid"], (1, 1, 1))
            xaxis, xstart, xend = self._expand_sale_axis(spec["cpids"][0], refine[0], spec["block"])
            yaxis, ystart, yend = self._expand_sale_axis(spec["cpids"][1], refine[1], spec["block"])
            zaxis, zstart, zend = self._expand_sale_axis(spec["cpids"][2], refine[2], spec["block"])
            nx, ny, nz = len(xaxis), len(yaxis), len(zaxis)
            element_count = max(nx - 1, 0) * max(ny - 1, 0) * max(nz - 1, 0)
            if element_count <= 0:
                raise ConversionError("SALE MSHID %s does not define a 3-D mesh" % spec["mshid"])
            if element_count > self.options.sale_max_elements:
                raise ConversionError(
                    "SALE MSHID %s expands to %s elements; configured limit is %s. "
                    "Increase 'SALE expansion limit' only if sufficient memory is available." %
                    (spec["mshid"], element_count, self.options.sale_max_elements))
            node_count = nx * ny * nz
            nbid = spec["nbid"] if spec["nbid"] > 0 else (
                max(self.model.nodes) + 1 if self.model.nodes else 1)
            ebid = spec["ebid"] if spec["ebid"] > 0 else (
                max(self.model.elements) + 1 if self.model.elements else 1)
            origin, basis = self._sale_orientation(spec)
            meta = {
                "mshid": spec["mshid"], "pid": spec["pid"],
                "nbid": nbid, "ebid": ebid,
                "nx": nx, "ny": ny, "nz": nz,
                "axes": (xaxis, yaxis, zaxis),
                "base_start": (xstart, ystart, zstart),
                "base_end": (xend, yend, zend),
                "refine": refine, "origin": origin, "basis": basis,
                "node_count": node_count, "element_count": element_count,
            }
            self.model.sale_meshes[spec["mshid"]] = meta
            layer_stride = max(nz // 25, 1)
            for k in range(nz):
                for j in range(ny):
                    for i in range(nx):
                        nid = self._sale_nid(meta, i, j, k)
                        if nid in self.model.nodes:
                            raise ConversionError("SALE generated node ID %s already exists" % nid)
                        self.model.nodes[nid] = self._sale_xyz(meta, xaxis[i], yaxis[j], zaxis[k])
                if k % layer_stride == 0:
                    fraction = (mesh_index + 0.45 * k / float(max(nz - 1, 1))) / mesh_count
                    self._progress(35 + int(12 * fraction), "Generating SALE nodes")
            for k in range(nz - 1):
                for j in range(ny - 1):
                    for i in range(nx - 1):
                        eid = self._sale_eid(meta, i, j, k)
                        if eid in self.model.elements:
                            raise ConversionError("SALE generated element ID %s already exists" % eid)
                        connectivity = (
                            self._sale_nid(meta, i, j, k),
                            self._sale_nid(meta, i + 1, j, k),
                            self._sale_nid(meta, i + 1, j + 1, k),
                            self._sale_nid(meta, i, j + 1, k),
                            self._sale_nid(meta, i, j, k + 1),
                            self._sale_nid(meta, i + 1, j, k + 1),
                            self._sale_nid(meta, i + 1, j + 1, k + 1),
                            self._sale_nid(meta, i, j + 1, k + 1),
                        )
                        self.model.elements[eid] = {
                            "id": eid, "pid": spec["pid"], "family": "SOLID",
                            "connectivity": connectivity,
                        }
                        self.model.element_sets[spec["pid"]].append(eid)
                if k % max((nz - 1) // 25, 1) == 0:
                    fraction = (mesh_index + 0.45 + 0.55 * k / float(max(nz - 2, 1))) / mesh_count
                    self._progress(35 + int(12 * fraction), "Generating SALE elements")
            self.report.message(
                "WARNING", "SALE_MESH_EXPANDED",
                "SALE MSHID %s expanded to %s nodes and %s elements" %
                (spec["mshid"], node_count, element_count), spec["block"])

    def _sale_element_nodes(self, meta, i, j, k):
        return (
            self._sale_nid(meta, i, j, k), self._sale_nid(meta, i + 1, j, k),
            self._sale_nid(meta, i + 1, j + 1, k), self._sale_nid(meta, i, j + 1, k),
            self._sale_nid(meta, i, j, k + 1), self._sale_nid(meta, i + 1, j, k + 1),
            self._sale_nid(meta, i + 1, j + 1, k + 1), self._sale_nid(meta, i, j + 1, k + 1))

    def _sale_face_nodes(self, meta, i, j, k, face):
        nodes = self._sale_element_nodes(meta, i, j, k)
        indices = {
            "-X": (0, 3, 7, 4), "+X": (1, 5, 6, 2),
            "-Y": (0, 4, 5, 1), "+Y": (3, 2, 6, 7),
            "-Z": (0, 1, 2, 3), "+Z": (4, 7, 6, 5),
        }[face]
        return tuple(nodes[index] for index in indices)

    def _sale_face_entities(self, meta, face, entity):
        nx, ny, nz = meta["nx"], meta["ny"], meta["nz"]
        if entity == "NODE":
            if face in ("-X", "+X"):
                i = 0 if face == "-X" else nx - 1
                return [self._sale_nid(meta, i, j, k) for k in range(nz) for j in range(ny)]
            if face in ("-Y", "+Y"):
                j = 0 if face == "-Y" else ny - 1
                return [self._sale_nid(meta, i, j, k) for k in range(nz) for i in range(nx)]
            k = 0 if face == "-Z" else nz - 1
            return [self._sale_nid(meta, i, j, k) for j in range(ny) for i in range(nx)]
        if face in ("-X", "+X"):
            i = 0 if face == "-X" else nx - 2
            cells = [(i, j, k) for k in range(nz - 1) for j in range(ny - 1)]
        elif face in ("-Y", "+Y"):
            j = 0 if face == "-Y" else ny - 2
            cells = [(i, j, k) for k in range(nz - 1) for i in range(nx - 1)]
        else:
            k = 0 if face == "-Z" else nz - 2
            cells = [(i, j, k) for j in range(ny - 1) for i in range(nx - 1)]
        if entity == "SOLID":
            return [self._sale_eid(meta, i, j, k) for i, j, k in cells]
        return [self._sale_face_nodes(meta, i, j, k, face) for i, j, k in cells]

    def _resolve_sale_sets(self):
        faces = ("-X", "+X", "-Y", "+Y", "-Z", "+Z")
        for spec in self.model.sale_set_specs:
            values = spec["values"]
            mshid = parse_int(values[0]) if values else 0
            meta = self.model.sale_meshes.get(mshid)
            if meta is None:
                self._warn(spec["block"], "SALE_SET_MESH_MISSING",
                           "SALE set SID %s references missing MSHID %s" % (spec["sid"], mshid))
                continue
            entity = spec["entity"]
            selected = []
            if spec["option"] == "SALEFAC":
                flags = [parse_int(value) for value in values[1:7]]
                flags.extend([0] * (6 - len(flags)))
                for face, flag in zip(faces, flags):
                    if flag:
                        selected.extend(self._sale_face_entities(meta, face, entity))
            else:
                bounds = [parse_int(value) for value in values[1:7]]
                if len(bounds) < 6:
                    self._warn(spec["block"], "SALECPT_INVALID",
                               "SALECPT SID %s needs six index bounds" % spec["sid"])
                    continue
                starts = meta["base_start"]
                refines = meta["refine"]
                local = []
                for axis in range(3):
                    low = (bounds[2 * axis] - starts[axis]) * refines[axis]
                    high = (bounds[2 * axis + 1] - starts[axis]) * refines[axis]
                    max_node = (meta[("nx", "ny", "nz")[axis]] - 1)
                    local.append((max(0, min(low, max_node)), max(0, min(high, max_node))))
                (ix0, ix1), (iy0, iy1), (iz0, iz1) = local
                if entity == "NODE":
                    selected = [self._sale_nid(meta, i, j, k)
                                for k in range(iz0, iz1 + 1)
                                for j in range(iy0, iy1 + 1)
                                for i in range(ix0, ix1 + 1)]
                elif entity == "SOLID":
                    selected = [self._sale_eid(meta, i, j, k)
                                for k in range(iz0, iz1)
                                for j in range(iy0, iy1)
                                for i in range(ix0, ix1)]
                else:
                    for face in faces:
                        if face == "-X":
                            cells = [(ix0, j, k) for k in range(iz0, iz1) for j in range(iy0, iy1)]
                        elif face == "+X":
                            cells = [(ix1 - 1, j, k) for k in range(iz0, iz1) for j in range(iy0, iy1)]
                        elif face == "-Y":
                            cells = [(i, iy0, k) for k in range(iz0, iz1) for i in range(ix0, ix1)]
                        elif face == "+Y":
                            cells = [(i, iy1 - 1, k) for k in range(iz0, iz1) for i in range(ix0, ix1)]
                        elif face == "-Z":
                            cells = [(i, j, iz0) for j in range(iy0, iy1) for i in range(ix0, ix1)]
                        else:
                            cells = [(i, j, iz1 - 1) for j in range(iy0, iy1) for i in range(ix0, ix1)]
                        selected.extend([self._sale_face_nodes(meta, i, j, k, face)
                                         for i, j, k in cells])
            if entity == "NODE":
                self.model.add_unique(self.model.node_sets, spec["sid"], selected)
            elif entity == "SOLID":
                self.model.add_unique(self.model.element_sets, "SET_%s" % spec["sid"], selected)
            else:
                current = self.model.segment_sets[spec["sid"]]
                seen = set(current)
                for segment in selected:
                    if segment not in seen:
                        current.append(segment)
                        seen.add(segment)

    def _sale_material_pid(self, group_number):
        index = parse_int(group_number) - 1
        if 0 <= index < len(self.model.ale_material_part_ids):
            return self.model.ale_material_part_ids[index]
        return 0

    def _sale_shape_contains(self, shape, point):
        params = shape["params"]
        if shape["type"] == 6 and len(params) >= 4:  # sphere
            return sum((point[i] - parse_float(params[i])) ** 2 for i in range(3)) <= parse_float(params[3]) ** 2
        if shape["type"] == 3 and len(params) >= 6:  # oriented half-space/plane
            origin = [parse_float(value) for value in params[:3]]
            normal = [parse_float(value) for value in params[3:6]]
            return sum((point[i] - origin[i]) * normal[i] for i in range(3)) >= 0.0
        if shape["type"] == 1 and len(params) >= 6:  # axis-aligned box fallback
            bounds = [parse_float(value) for value in params[:6]]
            return (min(bounds[0], bounds[1]) <= point[0] <= max(bounds[0], bounds[1]) and
                    min(bounds[2], bounds[3]) <= point[1] <= max(bounds[2], bounds[3]) and
                    min(bounds[4], bounds[5]) <= point[2] <= max(bounds[4], bounds[5]))
        return False

    def _resolve_sale_volume_fractions(self):
        if self.options.ale_mode not in ("AUTO", "EULERIAN"):
            return
        for block_index, block in enumerate(self.model.sale_volume_fraction_blocks):
            rows = _rows(block.lines)
            if not rows:
                continue
            header = rows[0]
            sid = parse_int(header[0])
            base_group = parse_int(header[2]) if len(header) > 2 else 0
            meta = None
            for candidate in self.model.sale_meshes.values():
                if candidate["pid"] == sid or candidate["mshid"] == sid:
                    meta = candidate
                    break
            if meta is None:
                self._warn(block, "SALE_VOLUME_MESH_MISSING",
                           "Volume-fraction SID %s has no generated SALE mesh" % sid)
                continue
            shapes = []
            cursor = 1
            while cursor < len(rows):
                shape_header = rows[cursor]
                if len(shape_header) < 3:
                    cursor += 1
                    continue
                shape_type = parse_int(shape_header[0])
                params = rows[cursor + 1] if cursor + 1 < len(rows) else []
                shapes.append({"type": shape_type,
                               "fillopt": parse_int(shape_header[1]),
                               "group": parse_int(shape_header[2]),
                               "params": params})
                cursor += 2
            groups = collections.defaultdict(list)
            xaxis, yaxis, zaxis = meta["axes"]
            for k in range(meta["nz"] - 1):
                for j in range(meta["ny"] - 1):
                    for i in range(meta["nx"] - 1):
                        local_center = ((xaxis[i] + xaxis[i + 1]) * 0.5,
                                        (yaxis[j] + yaxis[j + 1]) * 0.5,
                                        (zaxis[k] + zaxis[k + 1]) * 0.5)
                        point = self._sale_xyz(meta, local_center[0], local_center[1], local_center[2])
                        group = base_group
                        for shape in shapes:
                            if self._sale_shape_contains(shape, point):
                                group = shape["group"]
                        if group > 0:
                            groups[group].append(self._sale_eid(meta, i, j, k))
            for group, eids in groups.items():
                material_pid = self._sale_material_pid(group)
                part = self.model.parts.get(material_pid, {})
                material_id = part.get("material_id", 0)
                if material_pid <= 0 or material_id <= 0:
                    self._warn(block, "SALE_MATERIAL_GROUP_MISSING",
                               "SALE material group %s has no usable material part" % group)
                    continue
                set_key = "SALE_MAT_%s_%s_%s" % (meta["mshid"], block_index + 1, group)
                self.model.element_sets[set_key].extend(eids)
                self.model.eulerian_volume_fractions.append({
                    "elset": set_key, "material_id": material_id,
                    "material_part_id": material_pid,
                    "fraction": 1.0,
                })

    def _postprocess(self):
        self._generate_sale_meshes()
        self._resolve_sale_sets()
        self._resolve_sale_volume_fractions()
        # Expand part sets into element sets.
        for sid, part_ids in self.model.part_sets.items():
            eids = []
            for pid in part_ids:
                eids.extend(self.model.element_sets.get(pid, []))
            self.model.add_unique(self.model.element_sets, "PARTSET_%s" % sid, eids)
        # Create implicit part records when element PIDs have no *PART.
        pids = set([element["pid"] for element in self.model.elements.values()])
        for pid in sorted(pids):
            if pid not in self.model.parts:
                self.model.parts[pid] = {
                    "pid": pid, "title": "Implicit LS-DYNA part %s" % pid,
                    "name": "PART_%s" % pid, "section_id": 0, "material_id": 0,
                    "eos_id": 0, "hourglass_id": 0,
                }
                self.report.message("WARNING", "IMPLICIT_PART",
                                    "PID %s has elements but no *PART; a placeholder part definition was created" % pid)
        # Resolve and report the complete LS-DYNA reference chain.  References
        # are allowed to appear in any include file and in any source order, so
        # this check intentionally runs only after the full nested deck is read.
        for pid, part in self.model.parts.items():
            sid = part.get("section_id", 0)
            mid = part.get("material_id", 0)
            eosid = part.get("eos_id", 0)
            hgid = part.get("hourglass_id", 0)
            section = self.model.sections.get(sid)
            elform = section.get("elform", 0) if section else 0
            formulation = "CEL" if elform in CEL_SOLID_ELFORMS or pid in self.model.ale_mesh_part_ids else "LAGRANGIAN"
            self.report.message(
                "INFO", "PART_REFERENCE_CHAIN",
                "PID %s -> SECTION %s (ELFORM=%s, %s) -> MAT %s -> EROSION %s -> EOS %s -> HOURGLASS %s" %
                (pid, sid, elform, formulation, mid,
                 "YES" if mid in self.model.erosions else "NONE",
                 eosid or "NONE", hgid or "NONE"))
            if sid and section is None:
                self.report.message("WARNING", "SECTION_REFERENCE_MISSING",
                                    "PID %s references undefined SECTION %s" % (pid, sid))
            if mid and mid not in self.model.materials:
                self.report.message("WARNING", "MATERIAL_REFERENCE_MISSING",
                                    "PID %s references undefined MAT %s" % (pid, mid))
            if eosid and eosid not in self.model.eos:
                self.report.message("WARNING", "EOS_REFERENCE_MISSING",
                                    "PID %s references undefined EOS %s" % (pid, eosid))
            if hgid and hgid not in self.model.hourglasses:
                self.report.message("WARNING", "HOURGLASS_REFERENCE_MISSING",
                                    "PID %s references undefined HOURGLASS %s" % (pid, hgid))
        # Basic connectivity validation.
        missing = set()
        for element in self.model.elements.values():
            for nid in element["connectivity"]:
                if nid not in self.model.nodes:
                    missing.add(nid)
        if missing:
            preview = ", ".join([text_type(v) for v in sorted(missing)[:20]])
            self.report.message("ERROR", "MISSING_NODES",
                                "%s referenced nodes are undefined (first: %s)" % (len(missing), preview))
        if not self.model.nodes:
            self.report.message("ERROR", "NO_NODES", "No supported *NODE data were found")
        if not self.model.elements:
            self.report.message("ERROR", "NO_ELEMENTS", "No supported element data were found")


class AbaqusInpWriter(object):
    FACE_MAP = {
        "C3D8R": (("S1", (0, 1, 2, 3)), ("S2", (4, 7, 6, 5)),
                   ("S3", (0, 4, 5, 1)), ("S4", (1, 5, 6, 2)),
                   ("S5", (2, 6, 7, 3)), ("S6", (3, 7, 4, 0))),
        "EC3D8R": (("S1", (0, 1, 2, 3)), ("S2", (4, 7, 6, 5)),
                    ("S3", (0, 4, 5, 1)), ("S4", (1, 5, 6, 2)),
                    ("S5", (2, 6, 7, 3)), ("S6", (3, 7, 4, 0))),
        "C3D6": (("S1", (0, 1, 2)), ("S2", (3, 5, 4)),
                  ("S3", (0, 3, 4, 1)), ("S4", (1, 4, 5, 2)),
                  ("S5", (2, 5, 3, 0))),
        "C3D5": (("S1", (0, 1, 2, 3)), ("S2", (0, 4, 1)),
                  ("S3", (1, 4, 2)), ("S4", (2, 4, 3)), ("S5", (3, 4, 0))),
        "C3D4": (("S1", (0, 1, 2)), ("S2", (0, 3, 1)),
                  ("S3", (1, 3, 2)), ("S4", (2, 3, 0))),
    }

    def __init__(self, model, report, options, progress_callback=None):
        self.model = model
        self.report = report
        self.options = options
        self.progress_callback = progress_callback
        self._buffer = []
        self._buffer_limit = 8192
        self._handle = None
        self.generated_surfaces = collections.OrderedDict()
        self._face_index = None
        self._all_boundary_index = None
        self._contact_definitions = []
        self._general_contact = False
        self._active_pids = []
        self._nodes_by_pid = collections.OrderedDict()
        self._node_pids = collections.defaultdict(list)
        self._eid_pid = {}

    def _progress(self, percent, message):
        if self.progress_callback is not None:
            self.progress_callback(percent, message)

    def write(self, output_file):
        self._prepare_surfaces_and_contacts()
        self._build_part_indices()
        self._handle = io.open(output_file, "w", encoding="utf-8", newline="\n")
        try:
            self._heading()
            self._materials()
            self._section_controls()
            self._parts()
            self._assembly()
            self._amplitudes()
            self._initial_conditions()
            self._step()
            self._flush()
        finally:
            self._handle.close()
            self._handle = None
        self._progress(90, "Abaqus input file completed")

    def emit(self, line=""):
        self._buffer.append(text_type(line))
        if len(self._buffer) >= self._buffer_limit:
            self._flush()

    def _flush(self):
        if not self._buffer:
            return
        if self._handle is None:
            return
        self._handle.write("\n".join(self._buffer) + "\n")
        del self._buffer[:]

    def comment(self, text):
        clean = text_type(text).replace("\n", " ")
        self.emit("** %s" % clean[:240])

    def _heading(self):
        self.emit("*HEADING")
        self.emit(self.model.title)
        self.comment("Converted by LS-DYNA Keyword Importer for Abaqus/CAE 2020 v%s" % VERSION)
        self.comment("Unit system is unchanged. Review all approximated mappings before analysis.")

    def _part_name(self, pid):
        part = self.model.parts.get(pid, {})
        return _name("PART", pid, "PART_%s_%s" %
                     (pid, part.get("name", "PART_%s" % pid)))

    def _instance_name(self, pid):
        return _name("INSTANCE", pid, "INST_%s" % self._part_name(pid))

    def _build_part_indices(self):
        self._active_pids = []
        for pid, part in self.model.parts.items():
            eids = [eid for eid in self.model.element_sets.get(pid, [])
                    if eid in self.model.elements]
            if not eids:
                continue
            self._active_pids.append(pid)
            node_ids = set()
            ordered = []
            for eid in eids:
                element = self.model.elements[eid]
                self._eid_pid[eid] = pid
                for nid in element["connectivity"]:
                    if nid not in self.model.nodes:
                        continue
                    if nid not in node_ids:
                        node_ids.add(nid)
                        ordered.append(nid)
            self._nodes_by_pid[pid] = ordered
            for nid in ordered:
                self._node_pids[nid].append(pid)
        # Keep unattached but loadable LS-DYNA nodes in a real Abaqus part.
        if self._active_pids:
            first_pid = self._active_pids[0]
            for nid in self.model.nodes:
                if nid not in self._node_pids:
                    self._nodes_by_pid[first_pid].append(nid)
                    self._node_pids[nid].append(first_pid)

    def _parts(self):
        total_nodes = max(sum(len(values) for values in self._nodes_by_pid.values()), 1)
        written_nodes = 0
        total_elements = max(len(self.model.elements), 1)
        written_elements = 0
        for pid in self._active_pids:
            part = self.model.parts[pid]
            self.emit("*PART, NAME=%s" % self._part_name(pid))
            self.emit("*NODE, NSET=NALL")
            for nid in self._nodes_by_pid[pid]:
                xyz = self.model.nodes[nid]
                self.emit("%s, %s, %s, %s" %
                          (nid, _fmt(xyz[0]), _fmt(xyz[1]), _fmt(xyz[2])))
                written_nodes += 1
                if written_nodes % max(total_nodes // 100, 1) == 0:
                    self._progress(55 + int(10.0 * written_nodes / total_nodes),
                                   "Writing Abaqus parts and nodes")
            groups = collections.OrderedDict()
            for eid in self.model.element_sets.get(pid, []):
                if eid not in self.model.elements:
                    continue
                element = self.model.elements[eid]
                groups.setdefault(self._element_type(element), []).append(element)
            for element_type, elements in groups.items():
                self.emit("*ELEMENT, TYPE=%s, ELSET=%s" %
                          (element_type, self._pid_set_name(pid)))
                for element in elements:
                    self.emit("%s, %s" %
                              (element["id"], ", ".join(
                                  [text_type(n) for n in element["connectivity"]])))
                    written_elements += 1
                    if written_elements % max(total_elements // 100, 1) == 0:
                        self._progress(65 + int(12.0 * written_elements / total_elements),
                                       "Writing Abaqus part elements")
            self._write_section_for_part(pid, part)
            self.emit("*END PART")

    def _assembly(self):
        self.emit("*ASSEMBLY, NAME=ASSEMBLY")
        for pid in self._active_pids:
            self.emit("*INSTANCE, NAME=%s, PART=%s" %
                      (self._instance_name(pid), self._part_name(pid)))
            self.emit("*END INSTANCE")
        self._sets()
        self._surfaces()
        self.emit("*END ASSEMBLY")

    def _element_label(self, eid):
        pid = self._eid_pid.get(eid)
        if pid is None:
            return text_type(eid)
        return "%s.%s" % (self._instance_name(pid), eid)

    def _node_target_name(self, nid):
        return _name("NSET", nid, "DYNA_NODE_%s" % nid)

    def _element_type(self, element):
        family = element["family"]
        count = len(element["connectivity"])
        if family == "SOLID":
            if self._is_cel_pid(element["pid"]) and count == 8:
                return "EC3D8R"
            return {4: "C3D4", 5: "C3D5", 6: "C3D6", 8: "C3D8R"}.get(count, "C3D8R")
        if family == "TSHELL":
            return "SC8R" if count == 8 else {4: "C3D4", 6: "C3D6"}.get(count, "C3D8R")
        if family == "SHELL":
            return "S3" if count == 3 else "S4R"
        if family == "BEAM":
            return "B31"
        return "PC3D"

    def _pid_set_name(self, pid):
        part = self.model.parts.get(pid, {})
        title = part.get("name", "PART_%s" % pid)
        return _name("PID", pid, "PID_%s_%s" % (pid, title))

    def _nset_name(self, sid):
        return _name("NSET", sid)

    def _elset_name(self, sid):
        if isinstance(sid, text_type) or isinstance(sid, str):
            return _name("ELSET", sid)
        return _name("ELSET", sid)

    def _emit_value_chunks(self, values, size=16):
        chunk = []
        for value in values:
            chunk.append(text_type(value))
            if len(chunk) == size:
                self.emit(", ".join(chunk))
                chunk = []
        if chunk:
            self.emit(", ".join(chunk))

    def _sets(self):
        for pid in self._active_pids:
            eids = [eid for eid in self.model.element_sets.get(pid, [])
                    if eid in self.model.elements]
            self._emit_instance_set("ELSET", "EALL", pid, eids)
            self._emit_instance_set("ELSET", self._pid_set_name(pid), pid, eids)
            self._emit_instance_set("NSET", "NALL", pid,
                                    self._nodes_by_pid.get(pid, []))
        for sid, values in sorted(self.model.node_sets.items(), key=lambda item: text_type(item[0])):
            self._emit_node_set(self._nset_name(sid), values)
        for sid, values in sorted(self.model.element_sets.items(), key=lambda item: text_type(item[0])):
            if isinstance(sid, integer_types):
                continue
            grouped = collections.defaultdict(list)
            for eid in values:
                if eid in self._eid_pid:
                    grouped[self._eid_pid[eid]].append(eid)
            for pid in self._active_pids:
                self._emit_instance_set("ELSET", self._elset_name(sid), pid,
                                        grouped.get(pid, []))
        special_nodes = set()
        for item in self.model.initial_conditions:
            if item["kind"] == "NODE_VELOCITY":
                special_nodes.add(item["target"])
        for item in self.model.boundaries:
            if item["kind"] in ("SPC_NODE", "MOTION_NODE"):
                special_nodes.add(item["target"])
        for item in self.model.loads:
            if item["kind"] == "CLOAD_NODE":
                special_nodes.add(item["target"])
        for nid in sorted(special_nodes):
            self._emit_node_set(self._node_target_name(nid), [nid])
        if self.model.output["history_nodes"]:
            self._emit_node_set("DYNA_HISTORY_NODES",
                                sorted(set(self.model.output["history_nodes"])))
        if self.model.output["history_elements"]:
            grouped = collections.defaultdict(list)
            for eid in sorted(set(self.model.output["history_elements"])):
                if eid in self._eid_pid:
                    grouped[self._eid_pid[eid]].append(eid)
            for pid in self._active_pids:
                self._emit_instance_set("ELSET", "DYNA_HISTORY_ELEMENTS", pid,
                                        grouped.get(pid, []))
        if self.model.ale_keywords and self.options.ale_mode == "ADAPTIVE":
            grouped = collections.defaultdict(list)
            for element in self.model.elements.values():
                if (element["family"] in ("SOLID", "TSHELL") and
                        self._is_adaptive_pid(element["pid"])):
                    grouped[element["pid"]].append(element["id"])
            for pid in self._active_pids:
                self._emit_instance_set("ELSET", "ALE_DOMAIN", pid,
                                        grouped.get(pid, []))
        self._shared_node_constraints()
        self._progress(84, "Writing sets and surfaces")

    def _emit_instance_set(self, keyword, name, pid, values):
        values = list(values)
        if not values:
            return
        self.emit("*%s, %s=%s, INSTANCE=%s" %
                  (keyword, keyword, name, self._instance_name(pid)))
        self._emit_value_chunks(values)

    def _emit_node_set(self, name, node_ids):
        grouped = collections.defaultdict(list)
        seen = collections.defaultdict(set)
        for nid in node_ids:
            for pid in self._node_pids.get(nid, []):
                if nid not in seen[pid]:
                    grouped[pid].append(nid)
                    seen[pid].add(nid)
        for pid in self._active_pids:
            self._emit_instance_set("NSET", name, pid, grouped.get(pid, []))

    def _shared_node_constraints(self):
        """Reconnect duplicated Lagrangian nodes after splitting LS PIDs.

        The old implementation emitted three separate ``*EQUATION`` objects
        for every duplicated node pair.  Abaqus/CAE spends a long time turning
        those objects into model constraints.  A built-in TIE MPC expresses
        the original shared-node semantics in one data line and also handles
        shell rotations when both nodes have rotational degrees of freedom.

        Eulerian/CEL mesh nodes are deliberately excluded: their grid motion
        is not a Lagrangian part connection and tying them to structural nodes
        would change the CEL formulation.
        """
        mode = self.options.shared_node_mode
        shared = 0
        coupled = 0
        skipped = 0
        mpc_started = False
        candidates = [(nid, pids) for nid, pids in self._node_pids.items()
                      if len(pids) > 1]
        total = max(len(candidates), 1)
        stride = max(total // 100, 1)
        for index, item in enumerate(sorted(candidates)):
            nid, pids = item
            shared += 1
            lagrangian = [pid for pid in pids if not self._is_cel_pid(pid)]
            if len(lagrangian) < len(pids):
                skipped += 1
            if mode == "NONE" or len(lagrangian) < 2:
                if index % stride == 0:
                    self._progress(82 + int(2.0 * index / total),
                                   "Processing shared nodes")
                continue
            anchor = self._instance_name(lagrangian[0])
            for pid in lagrangian[1:]:
                other = self._instance_name(pid)
                if mode == "MPC":
                    if not mpc_started:
                        self.emit("*MPC")
                        mpc_started = True
                    # The first node is dependent.  Use each non-anchor copy
                    # only once as a dependent node and keep the anchor common.
                    self.emit("TIE, %s.%s, %s.%s" %
                              (other, nid, anchor, nid))
                else:
                    # Legacy compatibility mode: translation-only equations.
                    # This is intentionally slower and is no longer default.
                    for dof in (1, 2, 3):
                        self.emit("*EQUATION")
                        self.emit("2")
                        self.emit("%s.%s, %s, 1., %s.%s, %s, -1." %
                                  (other, nid, dof, anchor, nid, dof))
                coupled += 1
            if index % stride == 0:
                self._progress(82 + int(2.0 * index / total),
                               "Writing fast shared-node constraints")
        self.model.shared_node_labels = shared
        self.model.shared_node_constraints = coupled
        self.model.shared_node_skipped = skipped
        if not shared:
            return
        if mode == "MPC" and coupled:
            self.report.message(
                "WARNING", "SHARED_NODES_MPC_COUPLED",
                "%s shared node labels produced %s TIE MPC pairs; CEL-involved labels skipped=%s" %
                (shared, coupled, skipped))
        elif mode == "EQUATION" and coupled:
            self.report.message(
                "WARNING", "SHARED_NODES_EQUATION_COUPLED",
                "%s shared node labels produced %s legacy translational equation pairs; this mode is slow" %
                (shared, coupled))
        elif mode == "NONE":
            self.report.message(
                "WARNING", "SHARED_NODES_NOT_COUPLED",
                "%s shared node labels were duplicated without coupling by user selection" % shared)
        if skipped:
            self.report.message(
                "WARNING", "SHARED_NODES_CEL_NOT_COUPLED",
                "%s shared node labels involving CEL/Eulerian parts were not tied" % skipped)

    def _surfaces(self):
        for name, facets in self.generated_surfaces.items():
            if facets and facets[0][0] == "NODESET":
                self.emit("*SURFACE, TYPE=NODE, NAME=%s" % name)
                self.emit(facets[0][1])
                continue
            self.emit("*SURFACE, TYPE=ELEMENT, NAME=%s" % name)
            for eid, face in facets:
                self.emit("%s, %s" % (self._element_label(eid), face))

    def _materials(self):
        # A material can be referenced with different EOS IDs by different
        # parts.  Abaqus attaches EOS data to the material, so create a stable
        # material variant for each distinct (MID, EOSID) reference chain.
        chains = collections.OrderedDict()
        for pid, part in self.model.parts.items():
            mid = part.get("material_id", 0)
            if mid > 0:
                chains.setdefault((mid, part.get("eos_id", 0)), []).append(pid)
        referenced_mids = set([key[0] for key in chains])
        for mid in sorted(self.model.materials):
            if mid not in referenced_mids:
                chains.setdefault((mid, 0), [])
        for (mid, eosid), part_ids in chains.items():
            name = self._material_name(mid, eosid)
            material = self.model.materials.get(mid)
            if material is None:
                if self.options.create_placeholder_materials:
                    self._write_placeholder_material(mid, name)
                continue
            self._write_material(material, self.model.eos.get(eosid),
                                 name, part_ids)
        ale_group_has_material = any(
            self.model.parts.get(material_pid, {}).get("material_id", 0) > 0
            for material_pid in self.model.ale_material_part_ids)
        needs_zero = any(
            part.get("material_id", 0) == 0 and
            self.model.element_sets.get(pid, []) and
            (not self._is_cel_pid(pid) or not ale_group_has_material)
            for pid, part in self.model.parts.items())
        if needs_zero:
            self._write_placeholder_material(0)

    def _write_placeholder_material(self, mid, name=None):
        name = name or "MAT_%s_PLACEHOLDER" % mid
        self.emit("*MATERIAL, NAME=%s" % name)
        self.emit("*DENSITY")
        self.emit("1.")
        self.emit("*ELASTIC")
        self.emit("1.0E6, 0.3")
        self.comment("Placeholder material: replace density and stiffness before analysis")
        self.report.message("WARNING", "PLACEHOLDER_MATERIAL",
                            "Placeholder Abaqus material created for MID %s" % mid)

    def _material_name(self, mid, eosid=0):
        if mid in self.model.materials:
            title = self.model.materials[mid].get("title") or "MATERIAL"
            base = _name("MAT", mid, "MAT_%s_%s" % (mid, title))
        else:
            base = "MAT_%s_PLACEHOLDER" % mid
        if eosid:
            return _name("MAT", mid, "%s_EOS_%s" % (base, eosid))
        return base

    def _material_name_for_part(self, part):
        return self._material_name(part.get("material_id", 0),
                                   part.get("eos_id", 0))

    def _write_material(self, material, eos, name, part_ids):
        mid = material["id"]
        kind = material["kind"]
        rows = material["rows"]
        row0 = rows[0]
        self.emit("*MATERIAL, NAME=%s" % name)
        if kind in ("HIGH_EXPLOSIVE_BURN", "008"):
            rho = parse_float(row0[1], 1.0) if len(row0) > 1 else 1.0
            self.emit("*DENSITY")
            self.emit(_fmt(rho))
            if not self._write_eos(eos, material, rho, part_ids):
                detonation_speed = parse_float(row0[2], 1.0e3) if len(row0) > 2 else 1.0e3
                self.emit("*EOS, TYPE=USUP")
                self.emit("%s, 0., 0." % _fmt(max(detonation_speed, 1.0e-12)))
                self.comment("MAT_HIGH_EXPLOSIVE_BURN has no usable linked EOS; USUP fallback used")
                self.report.message("WARNING", "EXPLOSIVE_EOS_MISSING",
                                    "MID %s has no usable EOS; a USUP fallback was emitted" % mid)
        elif kind in ("ELASTIC", "001"):
            rho = parse_float(row0[1], 1.0) if len(row0) > 1 else 1.0
            young = parse_float(row0[2], 1.0e6) if len(row0) > 2 else 1.0e6
            poisson = parse_float(row0[3], 0.3) if len(row0) > 3 else 0.3
            self._density_elastic(rho, young, poisson)
        elif kind in ("PLASTIC_KINEMATIC", "003"):
            rho = parse_float(row0[1], 1.0)
            young = parse_float(row0[2], 1.0e6)
            poisson = parse_float(row0[3], 0.3)
            sigy = parse_float(row0[4], young * 1.0e-3) if len(row0) > 4 else young * 1.0e-3
            etan = parse_float(row0[5], 0.0) if len(row0) > 5 else 0.0
            self._density_elastic(rho, young, poisson)
            self.emit("*PLASTIC, HARDENING=COMBINED")
            self.emit("%s, 0." % _fmt(sigy))
            if etan > 0 and young > etan:
                plastic_modulus = young * etan / (young - etan)
                self.emit("%s, 1." % _fmt(sigy + plastic_modulus))
        elif kind in ("PIECEWISE_LINEAR_PLASTICITY", "024",
                      "ISOTROPIC_ELASTIC_PLASTIC", "012"):
            rho = parse_float(row0[1], 1.0)
            young = parse_float(row0[2], 1.0e6)
            poisson = parse_float(row0[3], 0.3)
            sigy = parse_float(row0[4], young * 1.0e-3) if len(row0) > 4 else young * 1.0e-3
            etan = parse_float(row0[5], 0.0) if len(row0) > 5 else 0.0
            lcss = parse_int(row0[8]) if len(row0) > 8 else 0
            self._density_elastic(rho, young, poisson)
            self.emit("*PLASTIC, HARDENING=ISOTROPIC")
            curve = self.model.curves.get(lcss)
            if curve and curve["points"]:
                for strain, stress in curve["points"]:
                    self.emit("%s, %s" % (_fmt(stress), _fmt(max(strain, 0.0))))
            else:
                self.emit("%s, 0." % _fmt(sigy))
                if etan > 0:
                    self.emit("%s, 1." % _fmt(sigy + etan))
        elif kind in ("JOHNSON_COOK", "015"):
            rho = parse_float(row0[1], 1.0)
            shear = parse_float(row0[2], 0.0)
            young = parse_float(row0[3], 1.0e6)
            poisson = parse_float(row0[4], 0.3)
            if young <= 0 and shear > 0:
                young = 2.0 * shear * (1.0 + poisson)
            self._density_elastic(rho, young, poisson)
            card2 = rows[1] if len(rows) > 1 else []
            a = parse_float(card2[0], young * 1.0e-3) if len(card2) > 0 else young * 1.0e-3
            b = parse_float(card2[1], 0.0) if len(card2) > 1 else 0.0
            n = parse_float(card2[2], 1.0) if len(card2) > 2 else 1.0
            c = parse_float(card2[3], 0.0) if len(card2) > 3 else 0.0
            m = parse_float(card2[4], 1.0) if len(card2) > 4 else 1.0
            tm = parse_float(card2[5], 1.0e30) if len(card2) > 5 else 1.0e30
            tr = parse_float(card2[6], 0.0) if len(card2) > 6 else 0.0
            eps0 = parse_float(card2[7], 1.0) if len(card2) > 7 else 1.0
            self.emit("*PLASTIC, HARDENING=JOHNSON COOK")
            self.emit("%s, %s, %s, %s, %s" % (_fmt(a), _fmt(b), _fmt(n), _fmt(m), _fmt(tm)))
            if c != 0.0:
                self.emit("*RATE DEPENDENT, TYPE=JOHNSON COOK")
                self.emit("%s, %s" % (_fmt(c), _fmt(eps0)))
            if tr:
                self.comment("LS-DYNA Johnson-Cook reference temperature: %s" % _fmt(tr))
        elif kind in ("NULL", "009"):
            rho = parse_float(row0[1], 1.0)
            self.emit("*DENSITY")
            self.emit(_fmt(rho))
            if not self._write_eos(eos, material, rho, part_ids):
                bulk = parse_float(row0[2], 1.0e6) if len(row0) > 2 else 1.0e6
                self.emit("*EOS, TYPE=USUP")
                self.emit("%s, 0., 0." % _fmt(math.sqrt(abs(bulk) / max(rho, 1.0e-30))))
        elif kind in ("POWER_LAW_PLASTICITY", "018"):
            rho = parse_float(row0[1], 1.0)
            young = parse_float(row0[2], 1.0e6)
            poisson = parse_float(row0[3], 0.3)
            strength = parse_float(row0[4], young * 1.0e-3) if len(row0) > 4 else young * 1.0e-3
            exponent = parse_float(row0[5], 1.0) if len(row0) > 5 else 1.0
            self._density_elastic(rho, young, poisson)
            self.emit("*PLASTIC, HARDENING=ISOTROPIC")
            self.emit("%s, 0." % _fmt(strength))
            self.emit("%s, 1." % _fmt(strength * (2.0 ** max(exponent, 0.0))))
        elif kind in ("MOONEY_RIVLIN_RUBBER", "027"):
            rho = parse_float(row0[1], 1.0)
            poisson = parse_float(row0[2], 0.499) if len(row0) > 2 else 0.499
            c10 = parse_float(row0[3], 1.0) if len(row0) > 3 else 1.0
            c01 = parse_float(row0[4], 0.0) if len(row0) > 4 else 0.0
            self.emit("*DENSITY")
            self.emit(_fmt(rho))
            self.emit("*HYPERELASTIC, MOONEY-RIVLIN")
            d1 = 0.0
            if poisson < 0.4999 and c10 + c01 > 0:
                bulk = 4.0 * (c10 + c01) * (1.0 + poisson) / max(3.0 * (1.0 - 2.0 * poisson), 1.0e-30)
                d1 = 2.0 / max(bulk, 1.0e-30)
            self.emit("%s, %s, %s" % (_fmt(c10), _fmt(c01), _fmt(d1)))
        elif kind in ("RIGID", "020"):
            rho = parse_float(row0[1], 1.0)
            young = parse_float(row0[2], 1.0e9) if len(row0) > 2 else 1.0e9
            poisson = parse_float(row0[3], 0.3) if len(row0) > 3 else 0.3
            self._density_elastic(rho, young, poisson)
            self.comment("MAT_RIGID requires manual rigid-body/reference-node review")
        else:
            rho = parse_float(row0[1], 1.0) if len(row0) > 1 else 1.0
            young = parse_float(row0[2], 1.0e6) if len(row0) > 2 else 1.0e6
            poisson = parse_float(row0[3], 0.3) if len(row0) > 3 else 0.3
            if young <= 0:
                young = 1.0e6
            if poisson <= -0.99 or poisson >= 0.5:
                poisson = 0.3
            self._density_elastic(rho, young, poisson)
            self.comment("Elastic fallback for LS-DYNA material %s" % kind)
            self.report.message("WARNING", "MATERIAL_ELASTIC_FALLBACK",
                                "MID %s (%s) was preserved with density and an elastic fallback" % (mid, kind))
        erosion = self.model.erosions.get(mid)
        if erosion is not None:
            self._write_erosion(material, eos, erosion)

    def _write_eos(self, eos, material, rho, part_ids):
        if eos is None:
            return False
        kind = eos["kind"]
        row = eos["rows"][0]
        if kind in ("JWL", "002"):
            source_material = material["rows"][0]
            if material["kind"] in ("HIGH_EXPLOSIVE_BURN", "008"):
                cd = parse_float(source_material[2], 0.0) if len(source_material) > 2 else 0.0
            else:
                bulk = parse_float(source_material[2], 0.0) if len(source_material) > 2 else 0.0
                cd = math.sqrt(abs(bulk) / max(rho, 1.0e-30)) if bulk else 1.0e-12
                self.report.message("WARNING", "JWL_DETONATION_SPEED_APPROX",
                                    "EOS %s is not paired with MAT_HIGH_EXPLOSIVE_BURN; Cd was estimated from the material bulk field" % eos["id"])
            a = parse_float(row[1], 0.0) if len(row) > 1 else 0.0
            b = parse_float(row[2], 0.0) if len(row) > 2 else 0.0
            r1 = parse_float(row[3], 0.0) if len(row) > 3 else 0.0
            r2 = parse_float(row[4], 0.0) if len(row) > 4 else 0.0
            omega = parse_float(row[5], 0.0) if len(row) > 5 else 0.0
            e0 = parse_float(row[6], 0.0) if len(row) > 6 else 0.0
            v0 = parse_float(row[7], 1.0) if len(row) > 7 else 1.0
            self.emit("*EOS, TYPE=JWL")
            self.emit("%s, %s, %s, %s, %s, %s, %s, 0." %
                      (_fmt(max(cd, 1.0e-12)), _fmt(a), _fmt(b), _fmt(omega),
                       _fmt(r1), _fmt(r2), _fmt(e0)))
            self._write_detonation_points(part_ids)
            if abs(v0 - 1.0) > 1.0e-12:
                self.report.message("WARNING", "JWL_INITIAL_VOLUME_REVIEW",
                                    "EOS %s uses LS-DYNA V0=%s; Abaqus JWL starts from the imported reference state" %
                                    (eos["id"], _fmt(v0)))
            return True
        if kind in ("GRUNEISEN", "004"):
            c0 = parse_float(row[1], 1.0e-12) if len(row) > 1 else 1.0e-12
            slope = parse_float(row[2], 0.0) if len(row) > 2 else 0.0
            gamma0 = parse_float(row[5], 0.0) if len(row) > 5 else 0.0
            self.emit("*EOS, TYPE=USUP")
            self.emit("%s, %s, %s" % (_fmt(max(c0, 1.0e-12)),
                                      _fmt(slope), _fmt(gamma0)))
            if any(abs(parse_float(row[index], 0.0)) > 0.0
                   for index in (3, 4) if len(row) > index):
                self.report.message("WARNING", "GRUNEISEN_HIGHER_ORDER_REVIEW",
                                    "EOS %s S2/S3 terms have no direct Abaqus USUP field" % eos["id"])
            return True
        if kind == "LINEAR_POLYNOMIAL":
            c1 = parse_float(row[2], 0.0) if len(row) > 2 else 0.0
            gamma0 = parse_float(row[5], 0.0) if len(row) > 5 else 0.0
            sound = math.sqrt(abs(c1) / max(rho, 1.0e-30))
            self.emit("*EOS, TYPE=USUP")
            self.emit("%s, 0., %s" % (_fmt(max(sound, 1.0e-12)), _fmt(gamma0)))
            self.report.message("WARNING", "LINEAR_POLYNOMIAL_TO_USUP",
                                "EOS %s was reduced to its initial bulk response" % eos["id"])
            return True
        # Preserve a runnable hydrodynamic material for EOS models without a
        # solver-equivalent Abaqus keyword and identify the approximation.
        bulk = parse_float(material["rows"][0][2], 1.0e6) if len(material["rows"][0]) > 2 else 1.0e6
        self.emit("*EOS, TYPE=USUP")
        self.emit("%s, 0., 0." % _fmt(math.sqrt(abs(bulk) / max(rho, 1.0e-30))))
        self.report.message("WARNING", "EOS_USUP_FALLBACK",
                            "EOS %s (%s) was preserved with an initial-bulk USUP approximation" %
                            (eos["id"], kind))
        return True

    def _write_detonation_points(self, part_ids):
        points = [item for item in self.model.initial_detonations
                  if not item.get("part_id") or item.get("part_id") in part_ids]
        if not points:
            coordinates = self._part_centroid(part_ids)
            points = [{"coordinates": coordinates, "delay": 0.0}]
            self.report.message("WARNING", "DETONATION_POINT_GENERATED",
                                "No matching INITIAL_DETONATION was found; a zero-delay part-centroid point was generated")
        self.emit("*DETONATION POINT")
        for item in points:
            xyz = item["coordinates"]
            self.emit("%s, %s, %s, %s" %
                      (_fmt(xyz[0]), _fmt(xyz[1]), _fmt(xyz[2]),
                       _fmt(item.get("delay", 0.0))))

    def _part_centroid(self, part_ids):
        node_ids = set()
        for pid in part_ids:
            for eid in self.model.element_sets.get(pid, []):
                if eid in self.model.elements:
                    node_ids.update(self.model.elements[eid]["connectivity"])
        points = [self.model.nodes[nid] for nid in node_ids if nid in self.model.nodes]
        if not points:
            return (0.0, 0.0, 0.0)
        return tuple(sum(point[index] for point in points) / len(points)
                     for index in range(3))

    def _write_erosion(self, material, eos, erosion):
        rows = erosion["rows"]
        row0 = list(rows[0]) + [""] * 8
        row1 = (list(rows[1]) if len(rows) > 1 else []) + [""] * 8
        effeps = parse_float(row0[4], 0.0)
        mnpres = parse_float(row1[0], 0.0)
        sigp1 = parse_float(row1[1], 0.0)
        mapped = []
        if eos is not None:
            cutoff = sigp1 if sigp1 > 0.0 else (-mnpres if mnpres < 0.0 else 0.0)
            if cutoff > 0.0:
                self.emit("*TENSILE FAILURE, ELEMENT DELETION=YES")
                self.emit(_fmt(cutoff))
                mapped.append("pressure/principal-stress cutoff")
        isotropic = material["kind"] in (
            "PIECEWISE_LINEAR_PLASTICITY", "024",
            "ISOTROPIC_ELASTIC_PLASTIC", "012",
            "POWER_LAW_PLASTICITY", "018")
        if isotropic and effeps > 0.0:
            self.emit("*SHEAR FAILURE, TYPE=TABULAR, ELEMENT DELETION=YES")
            self.emit("%s, 0., 0." % _fmt(effeps))
            mapped.append("effective plastic strain")
        criteria = {
            "MXPRES": parse_float(row0[2], 0.0),
            "MNEPS": parse_float(row0[3], 0.0),
            "EFFEPS": effeps,
            "VOLEPS": parse_float(row0[5], 0.0),
            "MNPRES": mnpres,
            "SIGP1": sigp1,
            "SIGVM": parse_float(row1[2], 0.0),
            "MXEPS": parse_float(row1[3], 0.0),
            "EPSSH": parse_float(row1[4], 0.0),
        }
        active = ["%s=%s" % (key, _fmt(value)) for key, value in criteria.items()
                  if abs(value) > 0.0]
        self.comment("MAT_ADD_EROSION MID %s: %s" %
                     (material["id"], ", ".join(active) if active else "no active scalar criteria"))
        self.report.message(
            "WARNING", "EROSION_MAPPING_SUMMARY",
            "MID %s mapped criteria: %s; review remaining criteria in the INP comments/report" %
            (material["id"], ", ".join(mapped) if mapped else "none"))

    def _density_elastic(self, rho, young, poisson):
        self.emit("*DENSITY")
        self.emit(_fmt(rho))
        self.emit("*ELASTIC")
        self.emit("%s, %s" % (_fmt(young), _fmt(poisson)))

    def _section_control_name(self, part, cel=False):
        hgid = part.get("hourglass_id", 0)
        if hgid <= 0 or hgid not in self.model.hourglasses:
            return ""
        return _name("HG", hgid, "HG_%s_%s" %
                     (hgid, "CEL" if cel else "LAGRANGIAN"))

    def _section_controls(self):
        definitions = collections.OrderedDict()
        for pid, part in self.model.parts.items():
            if not self.model.element_sets.get(pid, []):
                continue
            hgid = part.get("hourglass_id", 0)
            if hgid in self.model.hourglasses:
                cel = self._is_cel_pid(pid)
                definitions.setdefault((hgid, cel), self.model.hourglasses[hgid])
        for (hgid, cel), hourglass in definitions.items():
            name = self._section_control_name({"hourglass_id": hgid}, cel)
            ihq = hourglass.get("ihq", 0)
            if cel:
                formulation = "VISCOUS"
            elif ihq in (1, 2, 3):
                formulation = "VISCOUS"
            elif ihq in (6, 7, 8, 9, 10):
                formulation = "ENHANCED"
            else:
                formulation = "STIFFNESS"
            self.emit("*SECTION CONTROLS, NAME=%s, HOURGLASS=%s" %
                      (name, formulation))
            if formulation != "ENHANCED":
                stiffness_scale = min(max(hourglass.get("qm", 0.1) / 0.1, 0.2), 3.0)
                linear_scale = min(max(hourglass.get("q2", 0.06) / 0.06, 0.0), 3.0)
                quadratic_scale = min(max(hourglass.get("q1", 1.5) / 1.5, 0.0), 3.0)
                self.emit("%s, , , %s, %s" %
                          (_fmt(stiffness_scale), _fmt(linear_scale),
                           _fmt(quadratic_scale)))

    def _write_section_for_part(self, pid, part):
        eids = self.model.element_sets.get(pid, [])
        families = set([self.model.elements[eid]["family"] for eid in eids
                        if eid in self.model.elements])
        sid = part.get("section_id", 0)
        section = self.model.sections.get(sid, {})
        mat_name = self._material_name_for_part(part)
        elset = self._pid_set_name(pid)
        cel = self._is_cel_pid(pid) and "SOLID" in families
        controls_name = self._section_control_name(part, cel)
        controls = ", CONTROLS=%s" % controls_name if controls_name else ""
        self.comment("LS-DYNA PID %s / SECTION %s / ELFORM %s" %
                     (pid, sid, section.get("elform", "N/A")))
        if cel:
            self.emit("*EULERIAN SECTION, ELSET=%s%s" % (elset, controls))
            material_names = []
            if part.get("material_id", 0) > 0:
                material_names.append(mat_name)
            for material_pid in self.model.ale_material_part_ids:
                material_part = self.model.parts.get(material_pid, {})
                if material_part.get("material_id", 0) > 0:
                    name = self._material_name_for_part(material_part)
                    if name not in material_names:
                        material_names.append(name)
            if not material_names:
                material_names.append("MAT_0_PLACEHOLDER")
            for name in material_names:
                self.emit(name)
            return
        if "SHELL" in families:
            thickness = section.get("thickness", 1.0)
            self.emit("*SHELL SECTION, ELSET=%s, MATERIAL=%s%s" %
                      (elset, mat_name, controls))
            self.emit(_fmt(thickness))
        elif "BEAM" in families:
            area = section.get("area", 1.0)
            self.emit("*BEAM GENERAL SECTION, ELSET=%s, MATERIAL=%s, SECTION=GENERAL%s" %
                      (elset, mat_name, controls))
            self.emit("%s, %s, %s, %s, %s" %
                      (_fmt(area), _fmt(area), _fmt(area), _fmt(area), _fmt(area)))
            self.emit("0., 0., -1.")
        else:
            self.emit("*SOLID SECTION, ELSET=%s, MATERIAL=%s%s" %
                      (elset, mat_name, controls))

    def _amplitudes(self):
        for lcid, curve in sorted(self.model.curves.items()):
            if not curve["points"]:
                continue
            self.emit("*AMPLITUDE, NAME=CURVE_%s, TIME=TOTAL TIME" % lcid)
            row = []
            for time_value, ordinate in curve["points"]:
                row.extend([_fmt(time_value), _fmt(ordinate)])
                if len(row) == 8:
                    self.emit(", ".join(row))
                    row = []
            if row:
                self.emit(", ".join(row))

    def _initial_conditions(self):
        for index, item in enumerate(self.model.initial_conditions, 1):
            if item["kind"] == "NODE_VELOCITY":
                target = self._node_target_name(item["target"])
            else:
                target = self._pid_set_name(item["target"])
            values = list(item["values"]) + [0.0] * 6
            nonzero = [(dof, values[dof - 1]) for dof in range(1, 7)
                       if abs(values[dof - 1]) > 0.0]
            if nonzero:
                self.emit("*INITIAL CONDITIONS, TYPE=VELOCITY")
                for dof, value in nonzero:
                    self.emit("%s, %s, %s" % (target, dof, _fmt(value)))
        if self.model.eulerian_volume_fractions:
            self.emit("*INITIAL CONDITIONS, TYPE=VOLUME FRACTION")
            for item in self.model.eulerian_volume_fractions:
                material_part = self.model.parts.get(item.get("material_part_id", 0), {})
                material_name = (self._material_name_for_part(material_part)
                                 if material_part else
                                 self._material_name(item["material_id"]))
                self.emit("%s, %s, %s" %
                          (self._elset_name(item["elset"]),
                           material_name,
                           _fmt(item["fraction"])))

    def _step(self):
        self.emit("*STEP, NAME=LS_DYNA_EXPLICIT, NLGEOM=YES")
        self.emit("*DYNAMIC, EXPLICIT")
        self.emit(", %s" % _fmt(self.model.termination_time))
        if self.model.ale_keywords and self.options.ale_mode == "ADAPTIVE":
            if any(e["family"] in ("SOLID", "TSHELL") and self._is_adaptive_pid(e["pid"])
                   for e in self.model.elements.values()):
                self.emit("*ADAPTIVE MESH, ELSET=ALE_DOMAIN, FREQUENCY=1, MESH SWEEPS=1")
        self._write_boundaries()
        self._write_loads()
        self._write_contacts()
        self._write_output()
        self.emit("*END STEP")

    def _curve_clause(self, lcid):
        return ", AMPLITUDE=CURVE_%s" % lcid if lcid in self.model.curves else ""

    def _write_boundaries(self):
        for item in self.model.boundaries:
            kind = item["kind"]
            if kind.startswith("SPC"):
                target = (self._node_target_name(item["target"])
                          if kind == "SPC_NODE" else self._nset_name(item["target"]))
                for dof, flag in enumerate(item["flags"], 1):
                    if flag:
                        self.emit("*BOUNDARY")
                        self.emit("%s, %s, %s, 0." % (target, dof, dof))
            else:
                target = (self._node_target_name(item["target"])
                          if kind.endswith("NODE") else self._nset_name(item["target"]))
                vad = item.get("vad", 0)
                # LS-DYNA VAD: 0 velocity, 1 acceleration, 2 displacement.
                boundary_type = {0: "VELOCITY", 1: "ACCELERATION", 2: "DISPLACEMENT"}.get(vad, "VELOCITY")
                self.emit("*BOUNDARY, TYPE=%s%s" % (boundary_type, self._curve_clause(item["curve"])))
                self.emit("%s, %s, %s, %s" % (target, item["dof"], item["dof"], _fmt(item["scale"])))

    def _write_loads(self):
        for index, item in enumerate(self.model.loads, 1):
            if item["kind"].startswith("CLOAD"):
                target = (self._node_target_name(item["target"])
                          if item["kind"] == "CLOAD_NODE" else self._nset_name(item["target"]))
                self.emit("*CLOAD%s" % self._curve_clause(item["curve"]))
                self.emit("%s, %s, %s" % (target, item["dof"], _fmt(item["scale"])))
            elif item["kind"] == "BODY":
                vector = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}.get(item["axis"], (0, 0, 1))
                self.emit("*DLOAD%s" % self._curve_clause(item["curve"]))
                self.emit("EALL, GRAV, %s, %s, %s, %s" %
                          (_fmt(item["scale"]), vector[0], vector[1], vector[2]))
            elif item["kind"] == "SEGMENT_PRESSURE":
                facets = self._resolve_segments([item["nodes"]])
                if facets:
                    self.emit("*DLOAD%s" % self._curve_clause(item["curve"]))
                    for eid, face in facets:
                        load_label = "P" if face in ("SPOS", "SNEG") else "P%s" % face[1:]
                        self.emit("%s, %s, %s" %
                                  (self._element_label(eid), load_label,
                                   _fmt(item["scale"])))

    def _write_contacts(self):
        if self._general_contact:
            self.emit("*CONTACT")
            self.emit("*CONTACT INCLUSIONS, ALL EXTERIOR")
            self.emit("*CONTACT PROPERTY ASSIGNMENT")
            self.emit(", , GC_PROP")
        for definition in self._contact_definitions:
            if definition["type"] == "TIE":
                self.emit("*TIE, NAME=%s" % definition["name"])
                self.emit("%s, %s" % (definition["secondary"], definition["main"]))
            else:
                self.emit("*CONTACT PAIR, INTERACTION=%s, TYPE=SURFACE TO SURFACE" % definition["property"])
                self.emit("%s, %s" % (definition["secondary"], definition["main"]))

    def _write_output(self):
        output = self.model.output
        if not any([output["field"], output["nodal"], output["element"],
                    output["energy"], output["contact"], output["history_nodes"],
                    output["history_elements"]]):
            self.emit("*OUTPUT, FIELD, NUMBER INTERVAL=20")
            self.emit("*NODE OUTPUT")
            self.emit("U, V, A, RF")
            self.emit("*ELEMENT OUTPUT")
            if self._has_cel_elements():
                self.emit("EVF, SVAVG, PEEQVAVG")
            else:
                self.emit("S, LE, PEEQ")
            self.emit("*OUTPUT, HISTORY, TIME INTERVAL=%s" % _fmt(self.model.termination_time / 200.0))
            self.emit("*ENERGY OUTPUT")
            self.emit("ALLAE, ALLIE, ALLKE, ALLPD, ALLSE, ETOTAL")
            return
        field_clause = ""
        if output["field_interval"]:
            field_clause = ", TIME INTERVAL=%s" % _fmt(output["field_interval"])
        self.emit("*OUTPUT, FIELD%s" % field_clause)
        if output["nodal"] or output["field"]:
            self.emit("*NODE OUTPUT")
            self.emit("U, V, A, RF")
        if output["element"] or output["field"]:
            self.emit("*ELEMENT OUTPUT")
            if self._has_cel_elements():
                self.emit("EVF, SVAVG, PEEQVAVG")
            else:
                self.emit("S, LE, PE, PEEQ")
        history_clause = ""
        if output["history_interval"]:
            history_clause = ", TIME INTERVAL=%s" % _fmt(output["history_interval"])
        self.emit("*OUTPUT, HISTORY%s" % history_clause)
        if output["energy"]:
            self.emit("*ENERGY OUTPUT")
            self.emit("ALLAE, ALLIE, ALLKE, ALLPD, ALLSE, ETOTAL")
        if output["history_nodes"]:
            self.emit("*NODE OUTPUT, NSET=DYNA_HISTORY_NODES")
            self.emit("U, V, A, RF")
        if output["history_elements"]:
            self.emit("*ELEMENT OUTPUT, ELSET=DYNA_HISTORY_ELEMENTS")
            self.emit("S, LE, PEEQ")
        if output["contact"]:
            self.emit("*CONTACT OUTPUT")
            self.emit("CSTRESS, CDISP, CFT")

    def _is_cel_pid(self, pid):
        if self.options.ale_mode not in ("AUTO", "EULERIAN"):
            return False
        if pid in self.model.ale_mesh_part_ids:
            return True
        part = self.model.parts.get(pid, {})
        section = self.model.sections.get(part.get("section_id", 0), {})
        elform = section.get("elform", 0)
        if elform:
            return elform in CEL_SOLID_ELFORMS
        return False

    def _is_adaptive_pid(self, pid):
        identifiers = self.model.ale_part_ids | self.model.ale_mesh_part_ids
        if not identifiers:
            return True
        return pid in identifiers

    def _has_cel_elements(self):
        return any(element["family"] == "SOLID" and
                   self._is_cel_pid(element["pid"])
                   for element in self.model.elements.values())

    def _build_face_index(self):
        if self._face_index is not None:
            return
        self._face_index = collections.defaultdict(list)
        for eid, element in self.model.elements.items():
            etype = self._element_type(element)
            conn = element["connectivity"]
            if etype in ("S3", "S4R"):
                self._face_index[frozenset(conn)].append((eid, "SPOS", element["pid"]))
                continue
            for face, indices in self.FACE_MAP.get(etype, ()):
                try:
                    nodes = frozenset([conn[i] for i in indices])
                except IndexError:
                    continue
                self._face_index[nodes].append((eid, face, element["pid"]))

    def _resolve_segments(self, segments):
        self._build_face_index()
        facets = []
        for segment in segments:
            candidates = self._face_index.get(frozenset(segment), [])
            if candidates:
                eid, face, unused_pid = candidates[0]
                facets.append((eid, face))
            else:
                self.report.message("WARNING", "SEGMENT_FACE_NOT_FOUND",
                                    "Segment nodes %s do not match an imported element face" % (segment,))
        return facets

    def _boundary_facets_for_pids(self, pids):
        self._build_face_index()
        pids = set(pids)
        facets = []
        for candidates in self._face_index.values():
            selected = [item for item in candidates if item[2] in pids]
            # Within the selected region, one occurrence means an exterior face.
            if len(selected) == 1:
                facets.append((selected[0][0], selected[0][1]))
        facets.sort(key=lambda item: (item[0], item[1]))
        return facets

    def _entity_surface(self, entity_id, entity_type, role, contact_id):
        name = "%s_%s_%s" % (role, contact_id, entity_id)
        if entity_type == 4:
            nset = self._nset_name(entity_id)
            self.generated_surfaces[name] = [("NODESET", nset)]
            return name
        if entity_type == 0:
            facets = self._resolve_segments(self.model.segment_sets.get(entity_id, []))
        elif entity_type == 2:
            pids = self.model.part_sets.get(entity_id, [])
            if not pids and entity_id in self.model.parts:
                pids = [entity_id]
            facets = self._boundary_facets_for_pids(pids)
        elif entity_type == 3:
            facets = self._boundary_facets_for_pids([entity_id])
        elif entity_type in (5, 6):
            facets = self._boundary_facets_for_pids(self.model.parts.keys())
        else:
            facets = []
        if not facets:
            return None
        self.generated_surfaces[name] = facets
        return name

    def _prepare_surfaces_and_contacts(self):
        # User segment sets are always made available as surfaces.
        for sid, segments in sorted(self.model.segment_sets.items()):
            facets = self._resolve_segments(segments)
            if facets:
                self.generated_surfaces["SEGSET_%s" % sid] = facets

        properties = collections.OrderedDict()
        for contact in self.model.contacts:
            cid = contact["id"]
            kind = contact["kind"]
            pname = "CONTACT_PROP_%s" % cid
            properties[pname] = max(contact.get("fs", 0.0), 0.0)
            single = "SINGLE_SURFACE" in kind or "GENERAL" in kind
            if self.options.contact_mode == "GENERAL" or single:
                self._general_contact = True
                continue
            secondary = self._entity_surface(contact["ssid"], contact["sstyp"], "SEC", cid)
            main = self._entity_surface(contact["msid"], contact["mstyp"], "MAIN", cid)
            if not secondary or not main:
                self._general_contact = True
                self.report.message("WARNING", "CONTACT_FALLBACK_GENERAL",
                                    "Contact %s surfaces could not be resolved; all-exterior general contact is used" % cid)
                continue
            if "TIED" in kind:
                self._contact_definitions.append({"type": "TIE", "name": "TIE_%s" % cid,
                                                  "secondary": secondary, "main": main})
            else:
                self._contact_definitions.append({"type": "PAIR", "property": pname,
                                                  "secondary": secondary, "main": main})

        if self._general_contact:
            max_mu = max([c.get("fs", 0.0) for c in self.model.contacts] or [0.0])
            properties["GC_PROP"] = max_mu
        # Interaction properties are model data and must precede the step. Store
        # them for injection at the start of _materials.
        if properties:
            original_materials = self._materials

            def materials_with_interactions():
                for pname, friction in properties.items():
                    self.emit("*SURFACE INTERACTION, NAME=%s" % pname)
                    if friction > 0:
                        self.emit("*FRICTION")
                        self.emit(_fmt(friction))
                original_materials()
            self._materials = materials_with_interactions


def _write_json_report(path, payload):
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(text_type(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)))


def _html_escape(value):
    value = text_type(value)
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _write_html_report(path, payload):
    stats = payload["statistics"]
    rows = []
    for record in payload["keyword_records"]:
        rows.append("<tr class='%s'><td>%s</td><td>%s:%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" %
                    (_html_escape(record["status"]), _html_escape(record["keyword"]),
                     _html_escape(os.path.basename(record["source"])), record["line"],
                     _html_escape(record["status"]), _html_escape(record["target"]),
                     _html_escape(record["note"])))
    messages = []
    for item in payload["messages"]:
        messages.append("<li><b>%s %s</b>: %s</li>" %
                        (_html_escape(item.get("severity", "")), _html_escape(item.get("code", "")),
                         _html_escape(item.get("message", ""))))
    html = u"""<!doctype html>
<html><head><meta charset="utf-8"><title>LS-DYNA conversion report</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:28px;color:#1f2937}h1{margin-bottom:4px}
.sub{color:#6b7280}.cards{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.card{padding:12px 16px;border:1px solid #d1d5db;border-radius:8px;min-width:105px}.n{font-size:22px;font-weight:700}
table{border-collapse:collapse;width:100%%;font-size:13px}th,td{border:1px solid #d1d5db;padding:7px;text-align:left}th{background:#f3f4f6}.unsupported{background:#fee2e2}.approximated{background:#fef3c7}.converted{background:#ecfdf5}.ignored{background:#f3f4f6}code{background:#f3f4f6;padding:2px 4px}</style></head>
<body><h1>LS-DYNA → Abaqus/Explicit conversion report</h1><div class="sub">%s<br>Output: <code>%s</code></div>
<div class="cards">%s</div><h2>Messages</h2><ul>%s</ul><h2>Keyword mapping</h2>
<table><thead><tr><th>LS-DYNA keyword</th><th>Source</th><th>Status</th><th>Abaqus target</th><th>Note</th></tr></thead><tbody>%s</tbody></table></body></html>""" % (
        _html_escape(payload["source_file"]), _html_escape(payload["output_inp"]),
        "".join(["<div class='card'><div class='n'>%s</div>%s</div>" %
                 (stats.get(key, 0), label) for key, label in
                 (("nodes", "Nodes"), ("elements", "Elements"), ("converted", "Converted"),
                  ("approximated", "Approximated"), ("unsupported", "Unsupported"),
                  ("errors", "Errors"))]), "".join(messages), "".join(rows))
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(html)


def convert_keyword_file(source_file, output_inp, options=None,
                         progress_callback=None):
    options = options or ConverterOptions()
    source_file = os.path.abspath(path_text(source_file))
    output_inp = os.path.abspath(path_text(output_inp))
    progress_state = [-1, ""]

    def notify(percent, message):
        percent = max(0, min(parse_int(percent), 100))
        # Preserve Python 2 byte-string messages for Abaqus/AFX.  Keep a
        # Unicode copy only for stable duplicate suppression.
        message_text = text_type(message)
        if progress_callback is not None and (percent != progress_state[0] or
                                               message_text != progress_state[1]):
            progress_state[0] = percent
            progress_state[1] = message_text
            progress_callback(percent, message)

    notify(0, "Reading LS-DYNA keyword project")
    parser = DeckParser(recursive=options.recursive_includes)
    deck = parser.parse(source_file)
    notify(15, "Keyword project parsed")
    report = ConversionReport(source_file)
    reader = LsdynaSemanticReader(options, report, notify)
    model = reader.read(deck)
    report.finalize(model, deck)
    if report.statistics.get("errors", 0) and options.unsupported_policy == "STOP":
        raise ConversionError("Conversion stopped because validation errors were found")
    output_dir = os.path.dirname(output_inp)
    if output_dir and not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    writer = AbaqusInpWriter(model, report, options, notify)
    writer.write(output_inp)
    # Surface resolution and placeholder creation can add report messages.
    report.finalize(model, deck)
    base, unused = os.path.splitext(output_inp)
    payload = report.as_dict(output_inp, options, deck)
    json_path = base + "_conversion_report.json"
    html_path = base + "_conversion_report.html"
    if options.write_json:
        notify(92, "Writing JSON conversion report")
        _write_json_report(json_path, payload)
    else:
        json_path = None
    if options.write_html:
        notify(96, "Writing HTML conversion report")
        _write_html_report(html_path, payload)
    else:
        html_path = None
    result = {
        "input_file": source_file,
        "output_inp": output_inp,
        "json_report": json_path,
        "html_report": html_path,
        "statistics": report.statistics,
        "messages": report.messages,
    }
    notify(100, "Conversion completed")
    return result
