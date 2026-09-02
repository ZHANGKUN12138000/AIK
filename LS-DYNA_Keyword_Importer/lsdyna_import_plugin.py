# -*- coding: utf-8 -*-
"""Abaqus/CAE plug-in registration file (auto-loaded by CAE)."""
from abaqusGui import AFXMode, getAFXApp
from abaqusConstants import ALL

from lsdyna_import_form import LsdynaImportForm


toolset = getAFXApp().getAFXMainWindow().getPluginToolset()
toolset.registerGuiMenuButton(
    # Abaqus/CAE 2020 uses Python 2.7. AFX requires byte-string ``str``
    # values here and rejects ``unicode`` values (u"..."). Keep registration
    # metadata ASCII so it is reliable on every Windows locale.
    buttonText="LS-DYNA Keyword Importer v1.2.1...",
    object=LsdynaImportForm(toolset),
    messageId=AFXMode.ID_ACTIVATE,
    icon=None,
    kernelInitString="import lsdyna_import_kernel",
    applicableModules=ALL,
    version="1.2.1",
    author="OpenAI Codex",
    description=("Recursively reads LS-DYNA keyword projects and converts "
                 "mesh, sets, materials, loads, contact, ALE and output requests."),
    helpUrl="",
)
