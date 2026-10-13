"""API keys the user enters (Jooble, Careerjet), kept in the Windows Credential Manager and
never in the settings file. Passwords are never stored at all (PLAN §1)."""

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Protocol

from adawork import APP_NAME

log = logging.getLogger(__name__)

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2  # this user on this computer, kept across restarts
ERROR_NOT_FOUND = 1168


class Secrets(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class MemorySecrets:
    """For tests, and on systems without a credential store (kept until the app quits)."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsSecrets:
    """Generic credentials named "AdaWork/<name>" (visible in Control Panel → Credential
    Manager → Windows Credentials)."""

    def __init__(self, prefix: str = APP_NAME) -> None:
        self.prefix = prefix
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self._read, self._write = advapi.CredReadW, advapi.CredWriteW
        self._delete, self._free = advapi.CredDeleteW, advapi.CredFree
        self._read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_Credential)),
        ]
        self._write.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
        self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._free.argtypes = [ctypes.c_void_p]
        for function in (self._read, self._write, self._delete):
            function.restype = wintypes.BOOL

    def _target(self, name: str) -> str:
        return f"{self.prefix}/{name}"

    def get(self, name: str) -> str | None:
        found = ctypes.POINTER(_Credential)()
        if not self._read(self._target(name), CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
            if ctypes.get_last_error() != ERROR_NOT_FOUND:
                error = ctypes.WinError(ctypes.get_last_error())
                log.warning("Credential %s not readable: %s", name, error)
            return None
        try:
            cred = found.contents
            return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize).decode("utf-8")
        finally:
            self._free(found)

    def set(self, name: str, value: str) -> None:
        blob = value.encode("utf-8")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = _Credential(
            Type=CRED_TYPE_GENERIC,
            TargetName=self._target(name),
            CredentialBlobSize=len(blob),
            CredentialBlob=buffer,
            Persist=CRED_PERSIST_LOCAL_MACHINE,
            UserName=name,
        )
        if not self._write(ctypes.byref(cred), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def delete(self, name: str) -> None:
        deleted = self._delete(self._target(name), CRED_TYPE_GENERIC, 0)
        if not deleted and ctypes.get_last_error() != ERROR_NOT_FOUND:
            raise ctypes.WinError(ctypes.get_last_error())


def default_secrets() -> Secrets:
    if sys.platform == "win32":
        return WindowsSecrets()
    log.warning("No credential store on this system: API keys are kept until the app quits")
    return MemorySecrets()
