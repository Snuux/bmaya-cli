from __future__ import annotations

from types import ModuleType


def qt_gui() -> ModuleType:
    try:
        from PySide6 import QtGui
    except ImportError:
        from PySide2 import QtGui
    return QtGui


def qt_core() -> ModuleType:
    try:
        from PySide6 import QtCore
    except ImportError:
        from PySide2 import QtCore
    return QtCore
