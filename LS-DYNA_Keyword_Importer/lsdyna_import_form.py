# -*- coding: utf-8 -*-
"""AFX form for the LS-DYNA keyword importer."""
import io
import os
import tempfile
import traceback
import uuid

from abaqusGui import AFXBoolKeyword, AFXForm, AFXGuiCommand, AFXStringKeyword


class LsdynaImportForm(AFXForm):
    def __init__(self, owner):
        AFXForm.__init__(self, owner)
        self.cmd = AFXGuiCommand(mode=self, method="import_lsdyna",
                                 objectName="lsdyna_import_kernel",
                                 registerQuery=False)
        self.inputFileKw = AFXStringKeyword(self.cmd, "inputFile", True, "")
        self.outputInpKw = AFXStringKeyword(self.cmd, "outputInp", False, "")
        self._lastAutoOutput = ""
        self.modelNameKw = AFXStringKeyword(self.cmd, "modelName", True, "LS_DYNA_IMPORTED")
        self.recursiveKw = AFXBoolKeyword(self.cmd, "recursiveIncludes",
                                          AFXBoolKeyword.ON_OFF, True, True)
        self.aleModeKw = AFXStringKeyword(self.cmd, "aleMode", True, "AUTO")
        self.contactModeKw = AFXStringKeyword(self.cmd, "contactMode", True, "AUTO")
        self.unsupportedPolicyKw = AFXStringKeyword(self.cmd, "unsupportedPolicy", True, "REPORT")
        self.sharedNodeModeKw = AFXStringKeyword(self.cmd, "sharedNodeMode", True, "MPC")
        self.saleMaxElementsKw = AFXStringKeyword(self.cmd, "saleMaxElements", True, "5000000")
        self.placeholderKw = AFXBoolKeyword(self.cmd, "createPlaceholderMaterials",
                                            AFXBoolKeyword.ON_OFF, True, True)
        self.importKw = AFXBoolKeyword(self.cmd, "importIntoCae",
                                       AFXBoolKeyword.ON_OFF, True, True)
        # Hidden hand-off flag.  GUI-side conversion sets it before invoking
        # the standard AFXForm command pipeline so the kernel only imports the
        # already converted .inp file.
        self.preconvertedKw = AFXBoolKeyword(self.cmd, "preconverted",
                                              AFXBoolKeyword.ON_OFF, True, False)
        # Only this ASCII token crosses the GUI/kernel command channel.  The
        # real Unicode output path is stored in a UTF-8 hand-off file.
        self.handoffTokenKw = AFXStringKeyword(self.cmd, "handoffToken",
                                                False, "")

    def getFirstDialog(self):
        from lsdyna_import_dialog import LsdynaImportDialog
        return LsdynaImportDialog(self)

    def updateDefaultOutput(self, force=False):
        """Keep a visible, stable automatic INP path beside the selected K file."""
        from lsdyna_import_dialog import _afx_string

        input_value = self.inputFileKw.getValue().strip()
        if not input_value:
            return ""
        current = self.outputInpKw.getValue().strip()
        if not force and current and current != self._lastAutoOutput:
            return current
        input_file = os.path.abspath(input_value)
        stem = os.path.splitext(os.path.basename(input_file))[0]
        output_inp = os.path.join(os.path.dirname(input_file),
                                  stem + "_abaqus.inp")
        self._lastAutoOutput = _afx_string(output_inp)
        self.outputInpKw.setValue(self._lastAutoOutput)
        return output_inp

    def doCustomChecks(self):
        input_file = self.inputFileKw.getValue().strip()
        if not input_file:
            return False
        self.updateDefaultOutput()
        output_file = self.outputInpKw.getValue().strip()
        if output_file and not output_file.lower().endswith(".inp"):
            self.outputInpKw.setValue(output_file + ".inp")
        try:
            if int(self.saleMaxElementsKw.getValue().strip()) <= 0:
                return False
        except (TypeError, ValueError):
            return False
        return True

    def issueCommands(self, writeToReplay=True, writeToJournal=False):
        """Convert with GUI progress, then use the standard AFX command path."""
        from abaqusGui import getAFXApp, showAFXErrorDialog
        from lsdyna_import_dialog import LsdynaProgressDialog, _afx_string
        from lsk_converter import ConverterOptions, convert_keyword_file
        from lsk_parser import path_text

        stage = "initializing"
        owner = self.getCurrentDialog()
        progress = None
        handoff_path = None
        cleanup_handoff = True
        try:
            stage = "reading GUI values"
            input_file = os.path.abspath(self.inputFileKw.getValue().strip())
            self.updateDefaultOutput()
            output_value = self.outputInpKw.getValue().strip()
            if output_value:
                output_inp = os.path.abspath(output_value)
            else:
                stem = os.path.splitext(os.path.basename(input_file))[0]
                output_inp = os.path.join(os.path.dirname(input_file),
                                          stem + "_abaqus.inp")
                self.outputInpKw.setValue(_afx_string(output_inp))

            stage = "creating progress dialog"
            progress = LsdynaProgressDialog(owner)
            progress.create()
            progress.showModal(owner)
            getAFXApp().repaint()

            stage = "creating converter options"
            options = ConverterOptions(
                recursive_includes=self.recursiveKw.getValue(),
                ale_mode=self.aleModeKw.getValue(),
                contact_mode=self.contactModeKw.getValue(),
                unsupported_policy=self.unsupportedPolicyKw.getValue(),
                create_placeholder_materials=self.placeholderKw.getValue(),
                write_json=True,
                write_html=True,
                sale_max_elements=int(self.saleMaxElementsKw.getValue()),
                shared_node_mode=self.sharedNodeModeKw.getValue(),
            )
            import_into_cae = bool(self.importKw.getValue())

            def conversion_progress(percent, message):
                if import_into_cae:
                    percent = int(95.0 * percent / 100.0)
                progress.updateProgress(percent, message)

            stage = "converting LS-DYNA keywords"
            convert_keyword_file(input_file, output_inp, options,
                                 progress_callback=conversion_progress)

            stage = "creating GUI/kernel path hand-off"
            token = "lsdyna_import_%s_%s.path" % (os.getpid(), uuid.uuid4().hex)
            handoff_path = os.path.join(tempfile.gettempdir(), token)
            with io.open(handoff_path, "w", encoding="utf-8") as handle:
                handle.write(path_text(output_inp))
            self.handoffTokenKw.setValue(token)

            # AFXForm.issueCommands constructs a guaranteed native command
            # string and performs the normal mode cleanup.  The kernel sees
            # preconverted=True and therefore does not repeat conversion.
            self.preconvertedKw.setValue(True)
            if import_into_cae:
                progress.updateProgress(96, "Abaqus is importing the .inp; fast MPC mode avoids Equation expansion")
                getAFXApp().repaint()
                stage = "importing converted INP into the MDB"
            else:
                stage = "finalizing the standard AFX command"
            # From this point the kernel owns and removes the hand-off file.
            # This also remains correct if an Abaqus platform queues the
            # command instead of completing it before issueCommands returns.
            cleanup_handoff = False
            AFXForm.issueCommands(self, writeToReplay, writeToJournal)

            progress.updateProgress(100, "Completed")
            getAFXApp().repaint()
            progress.hide()
        except Exception:
            if progress is not None:
                progress.hide()
            detail = traceback.format_exc()
            print("LS-DYNA Keyword Importer v1.2.1 failed at stage: %s" % stage)
            print(detail)
            showAFXErrorDialog(
                owner,
                _afx_string("LS-DYNA Importer v1.2.1 failed\nStage: ") +
                _afx_string(stage) + _afx_string("\n\n") +
                _afx_string(detail),
            )
        finally:
            self.preconvertedKw.setValue(False)
            self.handoffTokenKw.setValue("")
            if cleanup_handoff and handoff_path and os.path.isfile(handoff_path):
                try:
                    os.remove(handoff_path)
                except OSError:
                    pass

    def okToCancel(self):
        return False
