# -*- coding: utf-8 -*-
"""Abaqus kernel entry point called by the AFX form."""
from __future__ import absolute_import, division, print_function

import os
import re
import shutil
import sys
import tempfile
import uuid
import io

from lsk_converter import ConverterOptions, convert_keyword_file


try:
    _unicode_type = unicode
except NameError:  # Python 3 test/runtime
    _unicode_type = None


def _abaqus_string(value):
    """Return the native ``str`` required by Abaqus 2020 kernel wrappers."""
    if _unicode_type is not None:
        if isinstance(value, str):
            return value
        if not isinstance(value, _unicode_type):
            value = _unicode_type(value)
        try:
            return value.encode("mbcs", "replace")
        except LookupError:
            return value.encode("utf-8", "replace")
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _console_string(value):
    """Return text that Python 2 can print on the active Abaqus console."""
    if _unicode_type is not None and isinstance(value, _unicode_type):
        # Abaqus replaces sys.stdout with a custom proxy.  On some 2020
        # installations its missing attributes are forwarded to the
        # ``abaqus`` module, producing "abaqus has no attribute encoding".
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


def _safe_model_name(value):
    value = value.strip() or "LS_DYNA_IMPORTED"
    value = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if value[0].isdigit():
        value = "M_" + value
    return value[:80]


def _unique_model_name(mdb, requested):
    if requested not in mdb.models:
        return requested
    index = 1
    while "%s_%s" % (requested, index) in mdb.models:
        index += 1
    return "%s_%s" % (requested, index)


def _handoff_path(token):
    """Resolve an ASCII basename without accepting path traversal."""
    token = _abaqus_string(token).strip()
    if (not token.startswith("lsdyna_import_") or
            not token.endswith(".path") or
            os.path.basename(token) != token or
            re.search(r"[^A-Za-z0-9_.-]", token)):
        raise ValueError("Invalid GUI/kernel hand-off token")
    return os.path.join(tempfile.gettempdir(), token)


def _read_handoff_output(token):
    path = _handoff_path(token)
    if not os.path.isfile(path):
        raise ValueError("GUI/kernel path hand-off file does not exist: %s" % path)
    with io.open(path, "r", encoding="utf-8") as handle:
        output_inp = handle.read().strip()
    if not output_inp:
        raise ValueError("GUI/kernel path hand-off file is empty")
    return os.path.abspath(output_inp), path


def import_converted_inp(outputInp, modelName="LS_DYNA_IMPORTED"):
    """Import an already converted input file into the current MDB."""
    from abaqus import mdb, session
    output_inp = os.path.abspath(outputInp.strip())
    if not os.path.isfile(output_inp):
        raise ValueError("Converted Abaqus input file does not exist: %s" % output_inp)
    requested = _abaqus_string(_safe_model_name(modelName))
    actual = _unique_model_name(mdb, requested)
    # Abaqus/CAE 2020 C++ wrappers reject Python 2 ``unicode`` values with
    # ``TypeError: not a string``.  Convert both API string arguments here,
    # immediately before crossing the kernel boundary.
    native_output = _abaqus_string(output_inp)
    staged_output = None
    if not os.path.isfile(native_output):
        # The Unicode path exists, but it cannot be represented by the active
        # Windows code page used by the Abaqus C++ wrapper.  Import an ASCII
        # hard-link/copy from the common temporary directory instead.
        staged_output = os.path.join(
            tempfile.gettempdir(),
            "lsdyna_import_stage_%s.inp" % uuid.uuid4().hex)
        try:
            if hasattr(os, "link"):
                os.link(output_inp, staged_output)
            else:
                raise OSError("hard links unavailable")
        except OSError:
            shutil.copyfile(output_inp, staged_output)
        native_output = staged_output
    try:
        mdb.ModelFromInputFile(name=_abaqus_string(actual),
                               inputFileName=_abaqus_string(native_output))
    finally:
        if staged_output and os.path.isfile(staged_output):
            try:
                os.remove(staged_output)
            except OSError:
                pass
    try:
        viewport = session.viewports[session.currentViewportName]
        viewport.setValues(displayedObject=mdb.models[actual].rootAssembly)
    except Exception:
        pass
    print("CAE model imported: %s" % actual)
    return actual


def import_lsdyna(inputFile, outputInp="", modelName="LS_DYNA_IMPORTED",
                  recursiveIncludes=True, aleMode="AUTO", contactMode="AUTO",
                  unsupportedPolicy="REPORT", createPlaceholderMaterials=True,
                  importIntoCae=True, saleMaxElements=5000000,
                  sharedNodeMode="MPC",
                  preconverted=False, handoffToken=""):
    if preconverted:
        handoff_path = None
        try:
            if handoffToken:
                output_inp, handoff_path = _read_handoff_output(handoffToken)
            elif outputInp.strip():
                output_inp = os.path.abspath(outputInp.strip())
            else:
                raise ValueError("No converted Abaqus input path was supplied")
            if not os.path.isfile(output_inp):
                raise ValueError("Converted Abaqus input file does not exist: %s" % output_inp)
            actual = None
            if importIntoCae:
                actual = import_converted_inp(output_inp, modelName)
            print("LS-DYNA pre-conversion accepted: %s" %
                  _console_string(output_inp))
            return {"output_inp": output_inp, "model_name": actual,
                    "preconverted": True}
        finally:
            if handoff_path and os.path.isfile(handoff_path):
                try:
                    os.remove(handoff_path)
                except OSError:
                    pass

    input_file = os.path.abspath(inputFile.strip())
    if not os.path.isfile(input_file):
        raise ValueError("LS-DYNA keyword file does not exist: %s" % input_file)
    if outputInp.strip():
        output_inp = os.path.abspath(outputInp.strip())
    else:
        stem = os.path.splitext(os.path.basename(input_file))[0]
        output_inp = os.path.join(os.path.dirname(input_file), stem + "_abaqus.inp")

    options = ConverterOptions(
        recursive_includes=recursiveIncludes,
        ale_mode=aleMode,
        contact_mode=contactMode,
        unsupported_policy=unsupportedPolicy,
        create_placeholder_materials=createPlaceholderMaterials,
        write_json=True,
        write_html=True,
        sale_max_elements=saleMaxElements,
        shared_node_mode=sharedNodeMode,
    )
    result = convert_keyword_file(input_file, output_inp, options)
    result["model_name"] = None

    if importIntoCae:
        actual = import_converted_inp(output_inp, modelName)
        result["model_name"] = actual

    stats = result["statistics"]
    print("=" * 72)
    print("LS-DYNA keyword import completed")
    print("Abaqus input: %s" % _console_string(result["output_inp"]))
    if result["model_name"]:
        print("CAE model: %s" % result["model_name"])
    print("Nodes=%s Elements=%s Converted=%s Approximated=%s Unsupported=%s Errors=%s" %
          (stats.get("nodes", 0), stats.get("elements", 0), stats.get("converted", 0),
           stats.get("approximated", 0), stats.get("unsupported", 0), stats.get("errors", 0)))
    print("HTML report: %s" % _console_string(result["html_report"]))
    print("=" * 72)
    return result
