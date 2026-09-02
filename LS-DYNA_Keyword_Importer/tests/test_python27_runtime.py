# -*- coding: utf-8 -*-
"""Regressions that require real Python 2 str/unicode semantics."""
from __future__ import absolute_import, division, print_function

import os
import io
import shutil
import sys
import tempfile
import types
import unittest
import uuid


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    unicode_type = unicode
except NameError:
    unicode_type = str


class _RootAssembly(object):
    pass


class _Model(object):
    def __init__(self):
        self.rootAssembly = _RootAssembly()


class _StrictMdb(object):
    def __init__(self):
        self.models = {}
        self.calls = []

    def ModelFromInputFile(self, name, inputFileName):
        # Reproduce the Abaqus 2020 wrapper's native-string requirement.
        if type(name) is not str or type(inputFileName) is not str:
            raise TypeError("not a string")
        self.calls.append((name, inputFileName))
        self.models[name] = _Model()


class _Viewport(object):
    def setValues(self, **kwargs):
        pass


class _Session(object):
    def __init__(self):
        self.currentViewportName = "Viewport: 1"
        self.viewports = {self.currentViewportName: _Viewport()}


class Python27RuntimeTests(unittest.TestCase):
    def test_console_string_does_not_require_stdout_encoding(self):
        import lsdyna_import_kernel
        import convert_k

        class AbaqusConsoleProxy(object):
            def __getattribute__(self, name):
                if name == "encoding":
                    raise AttributeError("module 'abaqus' has no attribute 'encoding'")
                return object.__getattribute__(self, name)

        original_stdout = sys.stdout
        try:
            sys.stdout = AbaqusConsoleProxy()
            kernel_text = lsdyna_import_kernel._console_string(u"\u8def\u5f84")
            cli_text = convert_k._console_string(u"\u8def\u5f84")
        finally:
            sys.stdout = original_stdout
        self.assertEqual(type(kernel_text), str)
        self.assertEqual(type(cli_text), str)

    def test_unicode_paths_with_nested_include_convert(self):
        from lsk_converter import ConverterOptions, convert_keyword_file

        temp_dir = tempfile.mkdtemp(prefix="lsk_py27_unicode_")
        try:
            project_dir = os.path.join(unicode_type(temp_dir),
                                       u"\u9879\u76ee\u8def\u5f84")
            shutil.copytree(os.path.join(HERE, "examples"), project_dir)
            source = os.path.join(project_dir, u"demo_main.k")
            output = os.path.join(project_dir, u"\u8f6c\u6362\u7ed3\u679c.inp")
            result = convert_keyword_file(source, output, ConverterOptions())
            self.assertTrue(os.path.isfile(output))
            self.assertGreater(result["statistics"]["nodes"], 0)
        finally:
            shutil.rmtree(temp_dir)

    def test_kernel_converts_unicode_before_abaqus_api(self):
        previous = sys.modules.pop("abaqus", None)
        temp_dir = tempfile.mkdtemp(prefix="lsk_py27_kernel_")
        try:
            output = os.path.join(temp_dir, "converted.inp")
            with open(output, "w") as handle:
                handle.write("*HEADING\n")
            strict_mdb = _StrictMdb()
            abaqus = types.ModuleType("abaqus")
            abaqus.mdb = strict_mdb
            abaqus.session = _Session()
            sys.modules["abaqus"] = abaqus

            import lsdyna_import_kernel
            actual = lsdyna_import_kernel.import_converted_inp(
                unicode_type(output), u"Model_\u6d4b\u8bd5")
            self.assertEqual(len(strict_mdb.calls), 1)
            name, input_file = strict_mdb.calls[0]
            self.assertEqual(type(name), str)
            self.assertEqual(type(input_file), str)
            self.assertEqual(type(actual), str)
        finally:
            sys.modules.pop("abaqus", None)
            if previous is not None:
                sys.modules["abaqus"] = previous
            shutil.rmtree(temp_dir)

    def test_kernel_handoff_preserves_unicode_output_path(self):
        previous = sys.modules.pop("abaqus", None)
        temp_dir = tempfile.mkdtemp(prefix="lsk_py27_handoff_")
        token = "lsdyna_import_test_%s.path" % uuid.uuid4().hex
        handoff_path = os.path.join(tempfile.gettempdir(), token)
        try:
            project_dir = os.path.join(unicode_type(temp_dir), u"\u8def\u5f84")
            os.makedirs(project_dir)
            output = os.path.join(project_dir, u"\u8f6c\u6362.inp")
            with io.open(output, "w", encoding="utf-8") as handle:
                handle.write(u"*HEADING\n")
            with io.open(handoff_path, "w", encoding="utf-8") as handle:
                handle.write(output)
            strict_mdb = _StrictMdb()
            abaqus = types.ModuleType("abaqus")
            abaqus.mdb = strict_mdb
            abaqus.session = _Session()
            sys.modules["abaqus"] = abaqus

            import lsdyna_import_kernel
            result = lsdyna_import_kernel.import_lsdyna(
                "", outputInp="", preconverted=True,
                handoffToken=token, importIntoCae=True)
            self.assertEqual(result["output_inp"], output)
            self.assertEqual(len(strict_mdb.calls), 1)
            self.assertFalse(os.path.isfile(handoff_path))
        finally:
            if os.path.isfile(handoff_path):
                os.remove(handoff_path)
            sys.modules.pop("abaqus", None)
            if previous is not None:
                sys.modules["abaqus"] = previous
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    unittest.main()
