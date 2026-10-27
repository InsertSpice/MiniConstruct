"""Operation-local preparation diagnostics; legacy lists are severity projections."""
from typing import Literal

from miniconstruct.execution import StrictModel


class PreparationDiagnostic(StrictModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str


class PreparationDiagnostics:
    def __init__(self):
        self.items: list[PreparationDiagnostic] = []

    def add(self, code: str, message: str, severity: Literal["error", "warning", "info"] = "warning"):
        # Keep first occurrence/order, but never let a lower severity mask an issue.
        rank = {"info": 0, "warning": 1, "error": 2}
        for index, item in enumerate(self.items):
            if item.message == message:
                if rank[severity] > rank[item.severity]:
                    self.items[index] = PreparationDiagnostic(code=code, message=message, severity=severity)
                return
        self.items.append(PreparationDiagnostic(code=code, message=message, severity=severity))

    def extend(self, code: str, messages: list[str], severity: Literal["error", "warning", "info"] = "warning"):
        for message in messages:
            self.add(code, message, severity)

    def messages(self, severity: str) -> list[str]:
        return [item.message for item in self.items if item.severity == severity]
