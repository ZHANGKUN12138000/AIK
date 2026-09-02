# -*- coding: utf-8 -*-
"""AFX data dialog. Kept separate so the kernel never imports GUI modules."""
from abaqusGui import *


try:
    _unicode_type = unicode
except NameError:  # Python 3 test/runtime
    _unicode_type = None


def _afx_string(value):
    """Return the native ``str`` required by Abaqus 2020 FOX widgets.

    Abaqus/CAE 2020 runs Python 2.7.  Its AFX wrappers reject ``unicode``
    objects with ``TypeError: not a string`` even when the text is ASCII.
    Keep this conversion at the GUI boundary so the converter can continue to
    use Unicode internally for paths and reports.
    """
    if _unicode_type is not None:
        if isinstance(value, str):
            return value
        if not isinstance(value, _unicode_type):
            value = _unicode_type(value)
        try:
            return value.encode("mbcs", "replace")
        except LookupError:  # Linux Abaqus does not provide the mbcs codec.
            return value.encode("utf-8", "replace")
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


class LsdynaImportDialog(AFXDataDialog):
    ID_INPUT = AFXDataDialog.ID_LAST
    ID_OUTPUT = ID_INPUT + 1

    def __init__(self, form):
        AFXDataDialog.__init__(
            self, form, "Import LS-DYNA Keyword Project v1.2.1",
            self.OK | self.CANCEL,
            DIALOG_ACTIONS_SEPARATOR,
        )
        self.form = form

        root = FXVerticalFrame(self, opts=LAYOUT_FILL_X | LAYOUT_FILL_Y,
                               x=0, y=0, w=0, h=0, pl=12, pr=12, pt=10, pb=10,
                               hs=8, vs=8)
        source_group = FXGroupBox(root, "Source and target model",
                                  FRAME_GROOVE | LAYOUT_FILL_X)
        matrix = FXMatrix(source_group, 3, opts=MATRIX_BY_COLUMNS | LAYOUT_FILL_X,
                          hs=6, vs=6)
        AFXTextField(matrix, 18, "Main .k file:", form.inputFileKw, 0)
        FXButton(matrix, "Browse...", None, self, self.ID_INPUT)
        FXLabel(matrix, "")

        AFXTextField(matrix, 18, "Output .inp (blank=automatic):", form.outputInpKw, 0)
        FXButton(matrix, "Browse...", None, self, self.ID_OUTPUT)
        FXLabel(matrix, "")

        AFXTextField(matrix, 18, "CAE model name:", form.modelNameKw, 0)
        FXLabel(matrix, "")
        FXLabel(matrix, "")

        option_group = FXGroupBox(root, "Conversion options", FRAME_GROOVE | LAYOUT_FILL_X)
        options = FXMatrix(option_group, 2, opts=MATRIX_BY_COLUMNS | LAYOUT_FILL_X,
                           hs=10, vs=7)
        FXLabel(options, "ALE strategy:")
        ale_combo = AFXComboBox(options, 38, 4, "", form.aleModeKw, 0,
                                LAYOUT_FILL_X)
        ale_combo.appendItem("AUTO")
        ale_combo.appendItem("ADAPTIVE")
        ale_combo.appendItem("EULERIAN")
        ale_combo.appendItem("IGNORE")

        FXLabel(options, "Contact strategy:")
        contact_combo = AFXComboBox(options, 38, 2, "", form.contactModeKw, 0,
                                    LAYOUT_FILL_X)
        contact_combo.appendItem("AUTO")
        contact_combo.appendItem("GENERAL")

        FXLabel(options, "Unsupported keywords:")
        policy_combo = AFXComboBox(options, 38, 2, "", form.unsupportedPolicyKw, 0,
                                   LAYOUT_FILL_X)
        policy_combo.appendItem("REPORT")
        policy_combo.appendItem("STOP")

        FXLabel(options, "Shared-node stitching:")
        shared_combo = AFXComboBox(options, 38, 3, "", form.sharedNodeModeKw, 0,
                                   LAYOUT_FILL_X)
        shared_combo.appendItem("MPC")
        shared_combo.appendItem("EQUATION")
        shared_combo.appendItem("NONE")

        FXLabel(options, "SALE expansion limit (elements):")
        AFXTextField(options, 16, "", form.saleMaxElementsKw, 0)

        check_frame = FXVerticalFrame(option_group, opts=LAYOUT_FILL_X)
        FXCheckButton(check_frame, "Read *INCLUDE / *INCLUDE_PATH recursively", form.recursiveKw, 0)
        FXCheckButton(check_frame, "Create placeholder materials when definitions are missing", form.placeholderKw, 0)
        FXCheckButton(check_frame, "Import converted model into the current MDB", form.importKw, 0)

        note = ("Units are unchanged. Multi-material ALE, erosion/failure and "
                "tiebreak contact require review in the generated HTML/JSON report.")
        FXLabel(root, note, opts=JUSTIFY_LEFT | LAYOUT_FILL_X)

        FXMAPFUNC(self, SEL_COMMAND, self.ID_INPUT, LsdynaImportDialog.onCmdInput)
        FXMAPFUNC(self, SEL_COMMAND, self.ID_OUTPUT, LsdynaImportDialog.onCmdOutput)

    def onCmdInput(self, sender, sel, ptr):
        dialog = AFXFileSelectorDialog(self, "Select the main LS-DYNA keyword file",
                                       self.form.inputFileKw, None,
                                       AFXSELECTFILE_EXISTING,
                                       patterns="LS-DYNA keyword (*.k,*.key)\nAll files (*)")
        dialog.create()
        dialog.showModal()
        self.form.updateDefaultOutput()
        return 1

    def onCmdOutput(self, sender, sel, ptr):
        dialog = AFXFileSelectorDialog(self, "Select the Abaqus input file",
                                       self.form.outputInpKw, None,
                                       AFXSELECTFILE_ANY,
                                       patterns="Abaqus input (*.inp)\nAll files (*)")
        dialog.create()
        dialog.showModal()
        return 1


class LsdynaProgressDialog(AFXDialog):
    """Determinate progress display for GUI-side conversion."""

    def __init__(self, owner):
        AFXDialog.__init__(self, owner, "Importing LS-DYNA project v1.2.1",
                           0, 0, DIALOG_ACTIONS_NONE)
        root = FXVerticalFrame(self, opts=LAYOUT_FILL_X,
                               x=0, y=0, w=0, h=0,
                               pl=14, pr=14, pt=12, pb=12, hs=8, vs=8)
        self.statusLabel = FXLabel(root, "Preparing conversion...",
                                   opts=JUSTIFY_LEFT | LAYOUT_FILL_X)
        self.progressBar = AFXProgressBar(
            root, None, 0,
            LAYOUT_FIX_WIDTH | LAYOUT_FIX_HEIGHT |
            FRAME_SUNKEN | FRAME_THICK | AFXPROGRESSBAR_ITERATOR,
            0, 0, 420, 22)
        self.progressBar.setTotal(100)
        self.progressBar.setProgress(0)

    def updateProgress(self, percent, message):
        self.statusLabel.setText(_afx_string(message))
        self.progressBar.setProgress(max(0, min(int(percent), 100)))
        getAFXApp().repaint()
