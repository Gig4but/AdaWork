"""What this computer can do (PLAN §3.2). Measured at every start in well under a second;
every resource decision is derived from the result instead of being hard-coded.

Graphics cards come from DXGI (the same list Windows' Task Manager shows): dedicated video
memory tells a real GPU from integrated graphics, which only borrows system RAM. AMD processors
may reserve many GB for their graphics ("UMA frame buffer"); those are known by their names.
"""

import ctypes
import logging
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

GIB = 1024**3
# Integrated graphics mostly reserve a few hundred MB to 2 GB ("UMA frame buffer").
DEDICATED_GPU_MIN = 3.5 * GIB
# AMD integrated graphics, which may reserve up to 16 GB: "AMD Radeon(TM) Graphics",
# "Radeon 780M", "Radeon 8060S", "Radeon Vega 8 Graphics", "Radeon R7 Graphics".
# Graphics cards say "RX" or "Pro" before the number.
INTEGRATED_NAMES = re.compile(
    r"^amd radeon(\(tm\))? graphics$|radeon(\(tm\))? (\d{3}m|\d{4}s)\b"
    r"|vega \d{1,2} graphics|radeon(\(tm\))? r[2-8] graphics"
)
VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x1022: "AMD", 0x8086: "Intel", 0x5143: "Qualcomm"}


@dataclass(frozen=True)
class Gpu:
    name: str
    vendor: str
    dedicated_bytes: int
    shared_bytes: int

    @property
    def is_dedicated(self) -> bool:
        integrated = INTEGRATED_NAMES.search(self.name.lower())
        return self.dedicated_bytes >= DEDICATED_GPU_MIN and not integrated

    @property
    def vram_gb(self) -> float:
        return self.dedicated_bytes / GIB


@dataclass(frozen=True)
class HardwareProfile:
    total_ram: int
    available_ram: int
    cpu_name: str
    physical_cores: int
    logical_cores: int
    avx2: bool
    avx512: bool
    gpus: tuple[Gpu, ...] = field(default_factory=tuple)
    free_disk: int = 0

    @property
    def ram_gb(self) -> float:
        return self.total_ram / GIB

    @property
    def available_gb(self) -> float:
        return self.available_ram / GIB

    @property
    def free_disk_gb(self) -> float:
        return self.free_disk / GIB

    @property
    def best_gpu(self) -> Gpu | None:
        return max(self.gpus, key=lambda g: (g.is_dedicated, g.dedicated_bytes), default=None)

    @property
    def dedicated_gpu(self) -> Gpu | None:
        gpu = self.best_gpu
        return gpu if gpu and gpu.is_dedicated else None

    def fingerprint(self) -> str:
        """Changes when the hardware does (RAM upgrade, new GPU), not with free memory."""
        gpu = self.best_gpu
        gpu_part = f"{gpu.name}/{round(gpu.vram_gb)}" if gpu else "-"
        return f"{round(self.ram_gb)}GB|{self.cpu_name}|{self.physical_cores}c|{gpu_part}"


def probe(disk_path: Path | None = None) -> HardwareProfile:
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(disk_path or Path.home()).free
    return HardwareProfile(
        total_ram=memory.total,
        available_ram=memory.available,
        cpu_name=_cpu_name(),
        physical_cores=psutil.cpu_count(logical=False) or 1,
        logical_cores=psutil.cpu_count(logical=True) or 1,
        avx2=_cpu_feature(40),  # PF_AVX2_INSTRUCTIONS_AVAILABLE
        avx512=_cpu_feature(41),  # PF_AVX512F_INSTRUCTIONS_AVAILABLE
        gpus=tuple(_gpus()),
        free_disk=disk,
    )


def available_ram() -> int:
    return psutil.virtual_memory().available


def _cpu_name() -> str:
    if sys.platform == "win32":
        import winreg

        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            with key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    import platform

    return platform.processor() or "CPU"


def _cpu_feature(feature: int) -> bool:
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(feature))


class _Luid(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_int32)]


class _AdapterDesc1(ctypes.Structure):
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", ctypes.c_uint),
        ("DeviceId", ctypes.c_uint),
        ("SubSysId", ctypes.c_uint),
        ("Revision", ctypes.c_uint),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", _Luid),
        ("Flags", ctypes.c_uint),
    ]


class _Guid(ctypes.Structure):
    _fields_ = [
        ("d1", ctypes.c_uint32),
        ("d2", ctypes.c_uint16),
        ("d3", ctypes.c_uint16),
        ("d4", ctypes.c_ubyte * 8),
    ]


# IID_IDXGIFactory1 {770aae78-f26f-4dba-a829-253c83d1b387}
_IID_FACTORY1 = _Guid(
    0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87)
)
_SOFTWARE_ADAPTER = 2
# vtable slots: IUnknown(3) + IDXGIObject(4) + IDXGIFactory(5) -> EnumAdapters1 = 12;
# IUnknown(3) + IDXGIObject(4) + IDXGIAdapter(3) -> GetDesc1 = 10.
_RELEASE, _ENUM_ADAPTERS1, _GET_DESC1 = 2, 12, 10


def _method(obj: ctypes.c_void_p, index: int, *argtypes):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return lambda *args: proto(vtable[index])(obj, *args)


def _gpus() -> list[Gpu]:
    if sys.platform != "win32":
        return []
    try:
        dxgi = ctypes.windll.dxgi
    except OSError:
        return []
    factory = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(_IID_FACTORY1), ctypes.byref(factory)) != 0:
        return []
    found: dict[str, Gpu] = {}
    try:
        enum = _method(factory, _ENUM_ADAPTERS1, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        index = 0
        while True:
            adapter = ctypes.c_void_p()
            if enum(index, ctypes.byref(adapter)) != 0:  # DXGI_ERROR_NOT_FOUND
                break
            index += 1
            desc = _AdapterDesc1()
            try:
                _method(adapter, _GET_DESC1, ctypes.POINTER(_AdapterDesc1))(ctypes.byref(desc))
            finally:
                _method(adapter, _RELEASE)()
            if desc.Flags & _SOFTWARE_ADAPTER:
                continue
            gpu = Gpu(
                name=desc.Description.strip(),
                vendor=VENDORS.get(desc.VendorId, f"0x{desc.VendorId:04x}"),
                dedicated_bytes=int(desc.DedicatedVideoMemory),
                shared_bytes=int(desc.SharedSystemMemory),
            )
            found.setdefault(f"{gpu.name}/{gpu.dedicated_bytes}", gpu)
    except OSError as exc:  # broken driver: carry on CPU-only
        log.warning("GPU probe failed: %s", exc)
    finally:
        _method(factory, _RELEASE)()
    return list(found.values())
