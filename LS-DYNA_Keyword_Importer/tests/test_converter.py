# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function

import io
import json
import os
import re
import shutil
import tempfile
import unittest


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from lsk_converter import ConversionError, ConverterOptions, convert_keyword_file
from lsk_parser import DeckParser


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.source = os.path.join(HERE, "examples", "demo_main.k")
        self.sale_source = os.path.join(HERE, "examples", "sale_demo.k")

    def test_recursive_includes_and_source_order(self):
        deck = DeckParser(recursive=True).parse(self.source)
        self.assertEqual(len(deck.files), 3)
        names = [block.name for block in deck.blocks]
        self.assertIn("NODE", names)
        self.assertIn("CONTACT_AUTOMATIC_SINGLE_SURFACE", names)
        self.assertLess(names.index("NODE"), names.index("CONTROL_TERMINATION"))

    def test_abaqus2020_gui_literals_are_python2_byte_strings(self):
        for filename in ("lsdyna_import_plugin.py", "lsdyna_import_dialog.py"):
            path = os.path.join(HERE, filename)
            with io.open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
            # Ignore comments, then reject u"..."/u'...' literals passed to
            # AFX. Abaqus 2020 wrappers require Python 2 ``str`` values.
            code_lines = [line for line in source.splitlines()
                          if not line.lstrip().startswith("#")]
            code = "\n".join(code_lines)
            self.assertIsNone(re.search(r"\bu[\"']", code), filename)
        with io.open(os.path.join(HERE, "lsdyna_import_plugin.py"),
                     "r", encoding="utf-8") as handle:
            registration = handle.read()
        self.assertIn('buttonText="LS-DYNA Keyword Importer v1.2.1..."',
                      registration)

    def test_abaqus2020_fxmatrix_uses_legacy_spacing_names(self):
        path = os.path.join(HERE, "lsdyna_import_dialog.py")
        with io.open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("hSpacing=", source)
        self.assertNotIn("vSpacing=", source)
        self.assertEqual(source.count("FXMatrix("), 2)
        self.assertIn("hs=6, vs=6", source)
        self.assertIn("hs=10, vs=7", source)

    def test_abaqus2020_gui_avoids_undefined_combobox_style(self):
        path = os.path.join(HERE, "lsdyna_import_dialog.py")
        with io.open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("AFXCOMBOBOX_NORMAL", source)
        self.assertEqual(source.count("AFXComboBox("), 4)

    def test_abaqus2020_gui_uses_fx_checkbutton(self):
        path = os.path.join(HERE, "lsdyna_import_dialog.py")
        with io.open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("AFXCheckButton", source)
        self.assertEqual(source.count("FXCheckButton("), 3)
        self.assertNotIn("AFXTEXTFIELD_STRING", source)

    def test_progress_bar_and_linear_element_collection_are_present(self):
        dialog_path = os.path.join(HERE, "lsdyna_import_dialog.py")
        converter_path = os.path.join(HERE, "lsk_converter.py")
        with io.open(dialog_path, "r", encoding="utf-8") as handle:
            dialog_source = handle.read()
        with io.open(converter_path, "r", encoding="utf-8") as handle:
            converter_source = handle.read()
        self.assertIn("AFXProgressBar(", dialog_source)
        self.assertIn("setText(_afx_string(message))", dialog_source)
        self.assertNotIn("message = text_type(message)", converter_source)
        self.assertIn("self.model.element_sets[pid].append(eid)", converter_source)
        self.assertNotIn("self.model.add_unique(self.model.element_sets, pid, [eid])",
                         converter_source)
        self.assertIn("self._buffer_limit = 8192", converter_source)

    def test_conversion_contains_core_mappings(self):
        temp_dir = tempfile.mkdtemp(prefix="lskimport_test_")
        try:
            output = os.path.join(temp_dir, "demo.inp")
            result = convert_keyword_file(self.source, output, ConverterOptions())
            with io.open(output, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("*NODE, NSET=NALL", text)
            self.assertIn("*ELEMENT, TYPE=C3D8R", text)
            self.assertIn("*NSET, NSET=NSET_10", text)
            self.assertIn("*SURFACE, TYPE=ELEMENT, NAME=SEGSET_20", text)
            self.assertIn("1, S2", text)
            self.assertIn("*BOUNDARY", text)
            self.assertIn("*CONTACT INCLUSIONS, ALL EXTERIOR", text)
            self.assertIn("*OUTPUT, FIELD, TIME INTERVAL=0.0001", text)
            self.assertEqual(result["statistics"]["nodes"], 8)
            self.assertEqual(result["statistics"]["elements"], 1)
            with io.open(result["json_report"], "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["statistics"]["source_files"], 3)
        finally:
            shutil.rmtree(temp_dir)

    def test_parameters_and_nested_include_name(self):
        temp_dir = tempfile.mkdtemp(prefix="lskimport_params_")
        try:
            include_file = os.path.join(temp_dir, "mesh_2.k")
            with io.open(include_file, "w", encoding="utf-8") as handle:
                handle.write(u"*NODE\n1,0,0,0\n*ELEMENT_SPH\n1,1,1\n")
            main_file = os.path.join(temp_dir, "main.k")
            with io.open(main_file, "w", encoding="utf-8") as handle:
                handle.write(u"*KEYWORD\n*PARAMETER\nI,IDX,2,R,SCALE,3.5\n"
                             u"*INCLUDE\nmesh_&IDX.k\n*END\n")
            deck = DeckParser(recursive=True).parse(main_file)
            self.assertEqual(deck.parameters["IDX"], 2)
            self.assertEqual(deck.parameters["SCALE"], 3.5)
            self.assertEqual(len(deck.files), 2)
        finally:
            shutil.rmtree(temp_dir)

    def test_ale_adaptive_domain_and_prescribed_motion_vad(self):
        temp_dir = tempfile.mkdtemp(prefix="lskimport_ale_")
        try:
            source = os.path.join(temp_dir, "ale.k")
            with io.open(source, "w", encoding="utf-8") as handle:
                handle.write(u"""*KEYWORD
*NODE
1,0,0,0
2,1,0,0
3,1,1,0
4,0,1,0
5,0,0,1
6,1,0,1
7,1,1,1
8,0,1,1
*ELEMENT_SOLID
1,1,1,2,3,4,5,6,7,8
*PART
ALE part
1,1,1
*SECTION_SOLID
1,1
*MAT_ELASTIC
1,1.,1000.,0.3
*SET_NODE_LIST
3
1,2
*DEFINE_CURVE
5
0,0
1,1
*BOUNDARY_PRESCRIBED_MOTION_SET
3,1,2,5,2.
*CONTROL_ALE
2,1
*CONTROL_TERMINATION
1.
*END
""")
            output = os.path.join(temp_dir, "ale.inp")
            convert_keyword_file(source, output, ConverterOptions(ale_mode="ADAPTIVE"))
            with io.open(output, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("*ELSET, ELSET=ALE_DOMAIN", text)
            self.assertIn("*ADAPTIVE MESH, ELSET=ALE_DOMAIN", text)
            self.assertIn("*BOUNDARY, TYPE=DISPLACEMENT, AMPLITUDE=CURVE_5", text)
        finally:
            shutil.rmtree(temp_dir)

    def test_initial_velocity_uses_dof_value_rows(self):
        temp_dir = tempfile.mkdtemp(prefix="lskimport_velocity_")
        try:
            source = os.path.join(temp_dir, "velocity.k")
            with io.open(source, "w", encoding="utf-8") as handle:
                handle.write(u"""*KEYWORD
*NODE
1,0,0,0
*ELEMENT_SPH
1,1,1
*PART
Particle
1,1,1
*SECTION_SOLID
1,1
*MAT_ELASTIC
1,1.,1000.,0.3
*INITIAL_VELOCITY_NODE
1,10.,20.,30.,0.,0.,0.
*END
""")
            output = os.path.join(temp_dir, "velocity.inp")
            convert_keyword_file(source, output, ConverterOptions())
            with io.open(output, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("1, 1, 10", text)
            self.assertIn("1, 2, 20", text)
            self.assertIn("1, 3, 30", text)
            self.assertNotIn("1, 10, 20, 30", text)
        finally:
            shutil.rmtree(temp_dir)

    def test_sale_structured_mesh_sets_volume_fraction_and_progress(self):
        temp_dir = tempfile.mkdtemp(prefix="lskimport_sale_")
        try:
            output = os.path.join(temp_dir, "sale.inp")
            progress = []

            def update(percent, message):
                progress.append((percent, message))

            result = convert_keyword_file(
                self.sale_source, output,
                ConverterOptions(ale_mode="EULERIAN", sale_max_elements=100),
                progress_callback=update)
            with io.open(output, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertEqual(result["statistics"]["sale_meshes"], 1)
            self.assertEqual(result["statistics"]["nodes"], 13)
            self.assertEqual(result["statistics"]["elements"], 2)
            self.assertIn("*ELEMENT, TYPE=EC3D8R", text)
            self.assertIn("*NSET, NSET=NSET_50", text)
            self.assertIn("*ELSET, ELSET=ELSET_SET_60", text)
            self.assertIn("*EULERIAN SECTION", text)
            self.assertIn("MAT_1_MATERIAL", text)
            self.assertIn("*INITIAL CONDITIONS, TYPE=VOLUME FRACTION", text)
            self.assertIn("ELSET_SALE_MAT_1_1_1, MAT_1_MATERIAL, 1", text)
            self.assertIn("NSET_50, 1, 1, 0.", text)
            self.assertEqual(progress[0][0], 0)
            self.assertEqual(progress[-1][0], 100)
            self.assertEqual([item[0] for item in progress],
                             sorted([item[0] for item in progress]))
        finally:
            shutil.rmtree(temp_dir)

    def test_sale_expansion_limit_stops_accidental_huge_mesh(self):
        temp_dir = tempfile.mkdtemp(prefix="lskimport_sale_limit_")
        try:
            output = os.path.join(temp_dir, "sale.inp")
            with self.assertRaises(ConversionError):
                convert_keyword_file(
                    self.sale_source, output,
                    ConverterOptions(ale_mode="EULERIAN", sale_max_elements=1))
        finally:
            shutil.rmtree(temp_dir)

    def test_auto_cel_preserves_parts_and_converts_material_chain(self):
        temp_dir = tempfile.mkdtemp(prefix="lskimport_chain_")
        try:
            source = os.path.join(temp_dir, "chain.k")
            with io.open(source, "w", encoding="utf-8") as handle:
                handle.write(u"""*KEYWORD
*NODE
1,0,0,0
2,1,0,0
3,1,1,0
4,0,1,0
5,0,0,1
6,1,0,1
7,1,1,1
8,0,1,1
11,2,0,0
12,3,0,0
13,3,1,0
14,2,1,0
15,2,0,1
16,3,0,1
17,3,1,1
18,2,1,1
*ELEMENT_SOLID
1,1,1,2,3,4,5,6,7,8
2,2,11,12,13,14,15,16,17,18
*PART
Lagrangian solid
1,1,1,0,10
*PART
Eulerian explosive
2,2,2,2,10
*SECTION_SOLID
1,1
*SECTION_SOLID
2,11
*MAT_PIECEWISE_LINEAR_PLASTICITY
1,1.,1000.,0.3,10.,1.,0.,0.,0
*MAT_ADD_EROSION
1,,0.,0.,0.25,0.,0.,1
*MAT_HIGH_EXPLOSIVE_BURN
2,1.,7000.,20.
*EOS_JWL
2,100.,10.,4.5,1.2,0.3,6.,1.
*HOURGLASS
10,5,0.1,,1.5,0.06
*INITIAL_DETONATION
2.5,0.5,0.5,0.01,2
*CONTROL_TERMINATION
0.1
*END
""")
            output = os.path.join(temp_dir, "chain.inp")
            result = convert_keyword_file(source, output, ConverterOptions())
            with io.open(output, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertEqual(text.count("*PART, NAME="), 2)
            self.assertEqual(text.count("*END PART"), 2)
            self.assertIn("*ELEMENT, TYPE=C3D8R, ELSET=PID_1_", text)
            self.assertIn("*ELEMENT, TYPE=EC3D8R, ELSET=PID_2_", text)
            self.assertEqual(text.count("*EULERIAN SECTION"), 1)
            self.assertIn("*EOS, TYPE=JWL", text)
            self.assertIn("7000, 100, 10, 0.3, 4.5, 1.2, 6, 0.", text)
            self.assertIn("*DETONATION POINT\n2.5, 0.5, 0.5, 0.01", text)
            self.assertIn("*SHEAR FAILURE, TYPE=TABULAR, ELEMENT DELETION=YES", text)
            self.assertEqual(text.count("*SECTION CONTROLS, NAME=HG_10_"), 2)
            self.assertIn("HOURGLASS=VISCOUS", text)
            self.assertIn("HOURGLASS=STIFFNESS", text)
            self.assertIn("*ASSEMBLY, NAME=ASSEMBLY", text)
            chain_messages = [item for item in result["messages"]
                              if item["code"] == "PART_REFERENCE_CHAIN"]
            self.assertEqual(len(chain_messages), 2)
            self.assertTrue(any("ELFORM=1, LAGRANGIAN" in item["message"]
                                for item in chain_messages))
            self.assertTrue(any("ELFORM=11, CEL" in item["message"]
                                for item in chain_messages))
        finally:
            shutil.rmtree(temp_dir)

    def test_fast_mpc_replaces_legacy_shared_node_equations(self):
        temp_dir = tempfile.mkdtemp(prefix="lskimport_shared_nodes_")
        try:
            source = os.path.join(temp_dir, "shared.k")
            with io.open(source, "w", encoding="utf-8") as handle:
                handle.write(u"""*KEYWORD
*NODE
1,0,0,0
2,1,0,0
3,1,1,0
4,0,1,0
5,0,0,1
6,1,0,1
7,1,1,1
8,0,1,1
9,2,0,0
10,2,1,0
11,2,0,1
12,2,1,1
*ELEMENT_SOLID
1,1,1,2,3,4,5,6,7,8
2,2,2,9,10,3,6,11,12,7
*PART
Left solid
1,1,1
*PART
Right solid
2,2,1
*SECTION_SOLID
1,1
*SECTION_SOLID
2,1
*MAT_ELASTIC
1,1.,1000.,0.3
*END
""")
            mpc_output = os.path.join(temp_dir, "mpc.inp")
            equation_output = os.path.join(temp_dir, "equation.inp")
            none_output = os.path.join(temp_dir, "none.inp")
            mpc_result = convert_keyword_file(source, mpc_output,
                                              ConverterOptions())
            equation_result = convert_keyword_file(
                source, equation_output,
                ConverterOptions(shared_node_mode="EQUATION"))
            none_result = convert_keyword_file(
                source, none_output,
                ConverterOptions(shared_node_mode="NONE"))
            with io.open(mpc_output, "r", encoding="utf-8") as handle:
                mpc_text = handle.read()
            with io.open(equation_output, "r", encoding="utf-8") as handle:
                equation_text = handle.read()
            with io.open(none_output, "r", encoding="utf-8") as handle:
                none_text = handle.read()

            self.assertEqual(mpc_text.count("*MPC"), 1)
            self.assertEqual(mpc_text.count("\nTIE, "), 4)
            self.assertNotIn("*EQUATION", mpc_text)
            self.assertEqual(equation_text.count("*EQUATION"), 12)
            self.assertNotIn("*MPC", equation_text)
            self.assertNotIn("*MPC", none_text)
            self.assertNotIn("*EQUATION", none_text)
            self.assertLess(len(mpc_text), len(equation_text))
            self.assertEqual(mpc_result["statistics"]["shared_node_labels"], 4)
            self.assertEqual(mpc_result["statistics"]["shared_node_constraints"], 4)
            self.assertTrue(any(item["code"] == "SHARED_NODES_MPC_COUPLED"
                                for item in mpc_result["messages"]))
            self.assertTrue(any(item["code"] == "SHARED_NODES_EQUATION_COUPLED"
                                for item in equation_result["messages"]))
            self.assertTrue(any(item["code"] == "SHARED_NODES_NOT_COUPLED"
                                for item in none_result["messages"]))
        finally:
            shutil.rmtree(temp_dir)

    def test_shared_nodes_involving_cel_are_not_kinematically_tied(self):
        temp_dir = tempfile.mkdtemp(prefix="lskimport_shared_cel_")
        try:
            source = os.path.join(temp_dir, "shared_cel.k")
            with io.open(source, "w", encoding="utf-8") as handle:
                handle.write(u"""*KEYWORD
*NODE
1,0,0,0
2,1,0,0
3,1,1,0
4,0,1,0
5,0,0,1
6,1,0,1
7,1,1,1
8,0,1,1
9,2,0,0
10,2,1,0
11,2,0,1
12,2,1,1
*ELEMENT_SOLID
1,1,1,2,3,4,5,6,7,8
2,2,2,9,10,3,6,11,12,7
*PART
Lagrangian solid
1,1,1
*PART
Eulerian domain
2,2,1
*SECTION_SOLID
1,1
*SECTION_SOLID
2,11
*MAT_ELASTIC
1,1.,1000.,0.3
*END
""")
            output = os.path.join(temp_dir, "shared_cel.inp")
            result = convert_keyword_file(source, output, ConverterOptions())
            with io.open(output, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn("*MPC", text)
            self.assertNotIn("*EQUATION", text)
            self.assertEqual(result["statistics"]["shared_node_labels"], 4)
            self.assertEqual(result["statistics"]["shared_node_constraints"], 0)
            self.assertEqual(result["statistics"]["shared_node_skipped"], 4)
            self.assertTrue(any(item["code"] == "SHARED_NODES_CEL_NOT_COUPLED"
                                for item in result["messages"]))
        finally:
            shutil.rmtree(temp_dir)

    def test_unknown_used_material_is_not_silently_dropped(self):
        temp_dir = tempfile.mkdtemp(prefix="lskimport_unknown_mat_")
        try:
            source = os.path.join(temp_dir, "unknown.k")
            with io.open(source, "w", encoding="utf-8") as handle:
                handle.write(u"""*KEYWORD
*NODE
1,0,0,0
*ELEMENT_SPH
1,1,1
*PART
Unknown material part
1,1,77
*SECTION_SOLID
1,1
*MAT_USER_DEFINED_MATERIAL_MODELS
77,2.5,1234.,0.25
*END
""")
            output = os.path.join(temp_dir, "unknown.inp")
            result = convert_keyword_file(source, output, ConverterOptions())
            with io.open(output, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("*MATERIAL, NAME=MAT_77_", text)
            self.assertIn("*DENSITY\n2.5", text)
            self.assertIn("*ELASTIC\n1234, 0.25", text)
            self.assertTrue(any(item["code"] == "MATERIAL_ELASTIC_FALLBACK"
                                for item in result["messages"]))
        finally:
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    unittest.main()
