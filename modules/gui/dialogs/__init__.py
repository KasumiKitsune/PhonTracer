"""
PhonTracer standalone dialogs package.
"""
from .about_dialog import AboutDialog
from .update_dialog import UpdateDialog
from .project_import_dialog import ProjectImportPreviewDialog
from .f0_detection_dialog import F0DetectionDialog
from .formant_detection_dialog import FormantDetectionDialog

__all__ = [
    "AboutDialog",
    "UpdateDialog",
    "ProjectImportPreviewDialog",
    "F0DetectionDialog",
    "FormantDetectionDialog",
]
