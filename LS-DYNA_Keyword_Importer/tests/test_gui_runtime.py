# -*- coding: utf-8 -*-
"""Strict GUI import/instantiation test using an Abaqus 2020 API-shaped stub.

This catches undefined AFX symbols and invalid constructor keyword arguments
without requiring an Abaqus license in the packaging environment.
"""
from __future__ import absolute_import, division, print_function

import importlib
import io
import os
import shutil
import sys
import tempfile
import types
import unittest


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


class _Keyword(object):
    def __init__(self, command, name, value):
        self.command = command
        self.name = name
        self.value = value

    def getValue(self):
        return self.value

    def setValue(self, value):
        self.value = value


class _AFXStringKeyword(_Keyword):
    def __init__(self, command, name, isRequired=False, defaultValue=""):
        _Keyword.__init__(self, command, name, defaultValue)
        self.isRequired = isRequired


class _AFXBoolKeyword(_Keyword):
    ON_OFF = 1
    TRUE_FALSE = 2

    def __init__(self, command, name, booleanType=ON_OFF,
                 isRequired=False, defaultValue=False):
        _Keyword.__init__(self, command, name, defaultValue)
        self.booleanType = booleanType
        self.isRequired = isRequired


class _AFXForm(object):
    def __init__(self, owner):
        self.owner = owner

    def getCurrentDialog(self):
        return getattr(self, "currentDialog", None)

    def deactivate(self):
        self.deactivated = True

    def issueCommands(self, writeToReplay=True, writeToJournal=False):
        self.baseIssueCount = getattr(self, "baseIssueCount", 0) + 1
        self.preconvertedDuringBaseIssue = self.preconvertedKw.getValue()
        self.handoffDuringBaseIssue = self.handoffTokenKw.getValue()
        handoff_path = os.path.join(tempfile.gettempdir(),
                                    self.handoffDuringBaseIssue)
        with io.open(handoff_path, "r", encoding="utf-8") as handle:
            self.handoffOutputDuringBaseIssue = handle.read().strip()
        os.remove(handoff_path)
        self.deactivate()


class _AFXGuiCommand(object):
    def __init__(self, mode, method, objectName, registerQuery=False):
        self.mode = mode
        self.method = method
        self.objectName = objectName
        self.registerQuery = registerQuery


class _AFXMode(object):
    ID_ACTIVATE = 100


class _AFXDialog(object):
    def __init__(self, owner, title, *args):
        self.owner = owner
        self.title = title
        self.visible = False

    def create(self):
        pass

    def showModal(self, owner=None):
        self.visible = True

    def hide(self):
        self.visible = False


class _AFXDataDialog(_AFXDialog):
    ID_LAST = 1000
    OK = 1
    CANCEL = 2

    def __init__(self, mode, title, actionButtonIds=0, opts=0):
        _AFXDialog.__init__(self, mode, title)
        self.mode = mode


class _Widget(object):
    instances = []

    def _save(self):
        type(self).instances.append(self)


class _FXVerticalFrame(_Widget):
    def __init__(self, p, opts=0, x=0, y=0, w=0, h=0, pl=0, pr=0,
                 pt=0, pb=0, hs=0, vs=0):
        self._save()


class _FXGroupBox(_Widget):
    def __init__(self, p, text="", opts=0, x=0, y=0, w=0, h=0,
                 pl=0, pr=0, pt=0, pb=0, hs=0, vs=0):
        self._save()


class _FXMatrix(_Widget):
    def __init__(self, p, n=1, opts=0, x=0, y=0, w=0, h=0, pl=0,
                 pr=0, pt=0, pb=0, hs=0, vs=0):
        self._save()


class _AFXTextField(_Widget):
    def __init__(self, p, ncols, label, tgt=None, sel=0, opts=0, x=0,
                 y=0, w=0, h=0, pl=0, pr=0, pt=0, pb=0):
        self._save()


class _FXButton(_Widget):
    def __init__(self, p, text, ic=None, tgt=None, sel=0, opts=0, x=0,
                 y=0, w=0, h=0, pl=0, pr=0, pt=0, pb=0):
        self._save()


class _FXLabel(_Widget):
    def __init__(self, p, text, ic=None, opts=0, x=0, y=0, w=0, h=0,
                 pl=0, pr=0, pt=0, pb=0):
        self.text = text
        self._save()

    def setText(self, text):
        if type(text) is not str:
            raise TypeError("not a string")
        self.text = text


class _AFXComboBox(_Widget):
    def __init__(self, p, ncols, nvis, label, tgt=None, sel=0, opts=0,
                 x=0, y=0, w=0, h=0, pl=0, pr=0, pt=0, pb=0):
        self.items = []
        self._save()

    def appendItem(self, text, data=None):
        self.items.append((text, data))


class _FXCheckButton(_Widget):
    instances = []

    def __init__(self, p, text, tgt=None, sel=0, opts=0, x=0, y=0,
                 w=0, h=0, pl=0, pr=0, pt=0, pb=0):
        self.text = text
        self.target = tgt
        self._save()


class _AFXProgressBar(_Widget):
    instances = []

    def __init__(self, p, keyword=None, tgt=None, opts=0, x=0, y=0,
                 w=0, h=0, pl=0, pr=0, pt=0, pb=0):
        self.total = 0
        self.progress = 0
        self._save()

    def setTotal(self, total):
        self.total = total

    def setProgress(self, progress):
        self.progress = progress


class _AFXFileSelectorDialog(object):
    instances = []

    def __init__(self, owner, title, pathNameKw, readOnlyKw, opts=0,
                 patterns="*", patternIndexTgt=None):
        self.owner = owner
        self.title = title
        self.opts = opts
        self.patterns = patterns
        type(self).instances.append(self)

    def create(self):
        pass

    def showModal(self):
        pass


class _Toolset(object):
    def __init__(self):
        self.registration = None

    def registerGuiMenuButton(self, buttonText, object, messageId=None,
                              icon=None, kernelInitString="",
                              applicableModules=None, version="", author="",
                              description="", helpUrl=""):
        if not isinstance(buttonText, str):
            raise TypeError("buttonText must be str")
        self.registration = buttonText


class _MainWindow(object):
    def __init__(self, toolset):
        self.toolset = toolset

    def getPluginToolset(self):
        return self.toolset


class _App(object):
    def __init__(self, toolset):
        self.main = _MainWindow(toolset)

    def getAFXMainWindow(self):
        return self.main

    def repaint(self):
        pass


_SENT_COMMANDS = []
_ERROR_MESSAGES = []


def _send_command(command, writeToReplay=True, writeToJournal=False):
    if type(command) is not str:
        raise TypeError("not a string")
    _SENT_COMMANDS.append(command)


def _show_error(owner, message):
    if type(message) is not str:
        raise TypeError("not a string")
    _ERROR_MESSAGES.append(message)


def _make_abaqus_gui(toolset):
    module = types.ModuleType("abaqusGui")
    exports = {
        "AFXMode": _AFXMode,
        "AFXForm": _AFXForm,
        "AFXGuiCommand": _AFXGuiCommand,
        "AFXStringKeyword": _AFXStringKeyword,
        "AFXBoolKeyword": _AFXBoolKeyword,
        "AFXDataDialog": _AFXDataDialog,
        "AFXDialog": _AFXDialog,
        "AFXTextField": _AFXTextField,
        "AFXComboBox": _AFXComboBox,
        "AFXProgressBar": _AFXProgressBar,
        "AFXFileSelectorDialog": _AFXFileSelectorDialog,
        "FXVerticalFrame": _FXVerticalFrame,
        "FXGroupBox": _FXGroupBox,
        "FXMatrix": _FXMatrix,
        "FXButton": _FXButton,
        "FXLabel": _FXLabel,
        "FXCheckButton": _FXCheckButton,
        "getAFXApp": lambda: _App(toolset),
        "sendCommand": _send_command,
        "showAFXErrorDialog": _show_error,
        "FXMAPFUNC": lambda *args: None,
        "LAYOUT_FILL_X": 1,
        "LAYOUT_FILL_Y": 2,
        "FRAME_GROOVE": 4,
        "MATRIX_BY_COLUMNS": 8,
        "JUSTIFY_LEFT": 16,
        "DIALOG_ACTIONS_SEPARATOR": 32,
        "DIALOG_ACTIONS_NONE": 0,
        "SEL_COMMAND": 64,
        "AFXSELECTFILE_EXISTING": 128,
        "AFXSELECTFILE_ANY": 256,
        "LAYOUT_FIX_WIDTH": 512,
        "LAYOUT_FIX_HEIGHT": 1024,
        "FRAME_SUNKEN": 2048,
        "FRAME_THICK": 4096,
        "AFXPROGRESSBAR_ITERATOR": 8192,
    }
    for name, value in exports.items():
        setattr(module, name, value)
    module.__all__ = list(exports.keys())
    return module


class GuiRuntimeCompatibilityTests(unittest.TestCase):
    MODULES = ("lsdyna_import_dialog", "lsdyna_import_form",
               "lsdyna_import_plugin")

    def setUp(self):
        self.previous = {}
        for name in self.MODULES + ("abaqusGui", "abaqusConstants"):
            self.previous[name] = sys.modules.pop(name, None)
        self.toolset = _Toolset()
        sys.modules["abaqusGui"] = _make_abaqus_gui(self.toolset)
        constants = types.ModuleType("abaqusConstants")
        constants.ALL = "ALL"
        sys.modules["abaqusConstants"] = constants
        _FXCheckButton.instances = []
        _AFXProgressBar.instances = []
        _AFXFileSelectorDialog.instances = []
        del _SENT_COMMANDS[:]
        del _ERROR_MESSAGES[:]

    def tearDown(self):
        for name in self.MODULES + ("abaqusGui", "abaqusConstants"):
            sys.modules.pop(name, None)
            if self.previous.get(name) is not None:
                sys.modules[name] = self.previous[name]

    def test_full_form_dialog_and_registration_instantiate(self):
        form_module = importlib.import_module("lsdyna_import_form")
        form = form_module.LsdynaImportForm(self.toolset)
        dialog = form.getFirstDialog()
        form.currentDialog = dialog
        self.assertEqual(dialog.title, "Import LS-DYNA Keyword Project v1.2.1")
        self.assertEqual(len(_FXCheckButton.instances), 3)
        self.assertEqual(dialog.onCmdInput(None, None, None), 1)
        self.assertEqual(dialog.onCmdOutput(None, None, None), 1)
        self.assertEqual(len(_AFXFileSelectorDialog.instances), 2)
        self.assertEqual(_AFXFileSelectorDialog.instances[0].opts, 128)
        self.assertEqual(_AFXFileSelectorDialog.instances[1].opts, 256)
        dialog_module = importlib.import_module("lsdyna_import_dialog")
        progress = dialog_module.LsdynaProgressDialog(dialog)
        progress.updateProgress(42, "Generating SALE mesh")
        self.assertEqual(progress.progressBar.total, 100)
        self.assertEqual(progress.progressBar.progress, 42)
        self.assertEqual(progress.statusLabel.text, "Generating SALE mesh")
        progress.updateProgress(43, b"Writing output")
        self.assertEqual(type(progress.statusLabel.text), str)
        self.assertEqual(progress.statusLabel.text, "Writing output")
        progress.updateProgress(44, u"Unicode progress text")
        self.assertEqual(type(progress.statusLabel.text), str)
        temp_dir = tempfile.mkdtemp(prefix="lsk_gui_progress_")
        try:
            form.inputFileKw.setValue(os.path.join(HERE, "examples", "demo_main.k"))
            automatic = form.updateDefaultOutput()
            self.assertEqual(automatic,
                             os.path.join(HERE, "examples", "demo_main_abaqus.inp"))
            self.assertEqual(form.outputInpKw.getValue(), automatic)
            output = os.path.join(temp_dir, "gui_conversion.inp")
            form.outputInpKw.setValue(output)
            form.importKw.setValue(True)
            form.issueCommands(True, False)
            self.assertTrue(os.path.isfile(output))
            self.assertTrue(form.deactivated)
            self.assertEqual(form.baseIssueCount, 1)
            self.assertTrue(form.preconvertedDuringBaseIssue)
            self.assertTrue(form.handoffDuringBaseIssue.startswith("lsdyna_import_"))
            self.assertEqual(form.handoffOutputDuringBaseIssue, output)
            self.assertFalse(form.preconvertedKw.getValue())
            self.assertEqual(_SENT_COMMANDS, [])
            self.assertEqual(_ERROR_MESSAGES, [])
        finally:
            shutil.rmtree(temp_dir)
        importlib.import_module("lsdyna_import_plugin")
        self.assertEqual(self.toolset.registration,
                         "LS-DYNA Keyword Importer v1.2.1...")

    def test_default_output_tracks_input_until_user_overrides_it(self):
        form_module = importlib.import_module("lsdyna_import_form")
        form = form_module.LsdynaImportForm(self.toolset)
        source1 = os.path.join(HERE, "examples", "demo_main.k")
        source2 = os.path.join(HERE, "examples", "sale_demo.k")
        form.inputFileKw.setValue(source1)
        output1 = form.updateDefaultOutput()
        self.assertEqual(output1,
                         os.path.join(HERE, "examples", "demo_main_abaqus.inp"))
        form.inputFileKw.setValue(source2)
        output2 = form.updateDefaultOutput()
        self.assertEqual(output2,
                         os.path.join(HERE, "examples", "sale_demo_abaqus.inp"))
        manual = os.path.join(HERE, "manual_target.inp")
        form.outputInpKw.setValue(manual)
        form.inputFileKw.setValue(source1)
        self.assertEqual(form.updateDefaultOutput(), manual)
        self.assertEqual(form.outputInpKw.getValue(), manual)


if __name__ == "__main__":
    unittest.main()
