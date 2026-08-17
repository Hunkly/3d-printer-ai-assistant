"""Typed error hierarchy for print-engineer."""

from __future__ import annotations

from typing import Any


class PrintEngineerError(Exception):
    """Base class for all print-engineer errors."""


class ConfigError(PrintEngineerError):
    """Raised when configuration cannot be loaded or is invalid."""


class InterfaceError(PrintEngineerError):
    """Raised when a backend adapter misbehaves."""


class PolicyViolation(PrintEngineerError):
    """Raised when an action is rejected by SafetyPolicy."""


class SlicerError(PrintEngineerError):
    """Base class for slicer-related errors.

    Every slicer error carries a stable machine-readable ``code`` plus a
    ``details`` mapping so MCP clients can react programmatically.
    """

    code = "slicer_error"

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        """Serialize the error into a machine-readable mapping."""
        return {"code": self.code, "message": str(self), "details": self.details}


class SlicerNotInstalled(SlicerError):
    """The slicer executable could not be found."""

    code = "slicer_not_installed"


class UnsupportedSlicerVersion(SlicerError):
    """The installed slicer version is too old for the requested operation."""

    code = "unsupported_slicer_version"


class SlicerUnavailable(SlicerError):
    """The slicer is installed but cannot perform the requested operation."""

    code = "slicer_unavailable"


class InvalidModel(SlicerError):
    """The input model file is missing, unsupported, or unreadable."""

    code = "invalid_model"


class InvalidProfile(SlicerError):
    """A slicer profile is malformed or incompatible with the job."""

    code = "invalid_profile"


class UnresolvedPrintContext(SlicerError):
    """A requested print context could not be resolved from local profiles.

    Raised instead of silently falling back to a generic/default printer,
    nozzle, process, or filament.
    """

    code = "unresolved_print_context"


class AmbiguousPrintContext(SlicerError):
    """A requested print context matched several distinct candidates.

    Raised instead of silently picking one. ``details.matches`` lists the
    candidates so a caller can ask the user to disambiguate.
    """

    code = "ambiguous_print_context"


class SliceFailed(SlicerError):
    """The slicer process ran but did not produce a valid slice result."""

    code = "slice_failed"


class SliceTimeout(SlicerError):
    """The slicer process exceeded the allowed time budget."""

    code = "slice_timeout"


class VersionMismatch(SlicerError):
    """The input project was created by a newer slicer than the installed one."""

    code = "version_mismatch"


class LLMError(PrintEngineerError):
    """Base class for local-LLM provider errors.

    Every LLM error carries a stable machine-readable ``code`` plus a
    ``details`` mapping so MCP clients can react programmatically.
    """

    code = "llm_error"

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        """Serialize the error into a machine-readable mapping."""
        return {"code": self.code, "message": str(self), "details": self.details}


class LLMUnavailable(LLMError):
    """The LLM provider could not be reached or returned an HTTP error."""

    code = "llm_unavailable"


class LLMTimeout(LLMError):
    """The LLM provider exceeded the allowed time budget."""

    code = "llm_timeout"


class LLMInvalidResponse(LLMError):
    """The LLM returned output that failed validation against the schema."""

    code = "llm_invalid_response"


class PrinterError(PrintEngineerError):
    """Base class for printer-related errors.

    Every printer error carries a stable machine-readable ``code`` plus a
    ``details`` mapping so MCP clients can react programmatically.
    """

    code = "printer_error"

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        """Serialize the error into a machine-readable mapping."""
        return {"code": self.code, "message": str(self), "details": self.details}


class PrinterNotConfigured(PrinterError):
    """Connection parameters (host/serial/access code) are missing."""

    code = "printer_not_configured"


class PrinterUnreachable(PrinterError):
    """The printer could not be reached over the network (TCP/TLS)."""

    code = "printer_unreachable"


class PrinterAuthFailed(PrinterError):
    """The MQTT broker rejected the access code (CONNACK rc 4/5)."""

    code = "printer_auth_failed"


class PrinterTimeout(PrinterError):
    """No status report was received within the allowed time budget."""

    code = "printer_timeout"


class PrinterInvalidReport(PrinterError):
    """The printer payload was not valid JSON or not a status report."""

    code = "printer_invalid_report"


class PrinterOperationUnsupported(PrinterError):
    """The operation is not supported by the read-only printer increment."""

    code = "printer_operation_unsupported"
