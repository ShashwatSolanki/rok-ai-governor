"""Read-only diagnostics for the native Windows game process.

This module queries process metadata and enumerates already-loaded module names.
It does not read process memory, write to the process, inject code, or send input.
"""
from __future__ import annotations

import ctypes
import os
import re
from ctypes import wintypes
from typing import Any


class NativeProcessDiagnosticError(RuntimeError):
    """The selected game process could not be inspected."""


_INPUT_MODULE = re.compile(
    r"(input|hid|dinput|xinput|gameinput|winmm|user32|unity|"
    r"directinput|rawinput|ucrt|kernelbase)",
    re.IGNORECASE,
)


def _pe_machine(path: str) -> str:
    """Read the PE machine field from the executable header without loading it."""
    try:
        with open(path, "rb") as stream:
            if stream.read(2) != b"MZ":
                return "unknown (not a PE image)"
            stream.seek(0x3C)
            offset_data = stream.read(4)
            if len(offset_data) != 4:
                return "unknown (truncated DOS header)"
            pe_offset = int.from_bytes(offset_data, "little")
            if pe_offset > 64 * 1024 * 1024:
                return "unknown (invalid PE offset)"
            stream.seek(pe_offset)
            if stream.read(4) != b"PE\0\0":
                return "unknown (missing PE signature)"
            machine_data = stream.read(2)
            if len(machine_data) != 2:
                return "unknown (truncated PE header)"
            machine = int.from_bytes(machine_data, "little")
        return {
            0x014C: "x86",
            0x8664: "x64",
            0xAA64: "ARM64",
            0x01C4: "ARM",
        }.get(machine, f"unknown (PE machine 0x{machine:04X})")
    except (OSError, ValueError):
        return "unavailable"


def _enumerate_modules(process: wintypes.HANDLE) -> dict[str, Any]:
    """List module paths using PSAPI; gracefully report access restrictions."""
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    enum_modules = psapi.EnumProcessModulesEx
    enum_modules.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HMODULE),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
    ]
    enum_modules.restype = wintypes.BOOL
    get_module_name = psapi.GetModuleFileNameExW
    get_module_name.argtypes = [
        wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD
    ]
    get_module_name.restype = wintypes.DWORD

    needed = wintypes.DWORD()
    # LIST_MODULES_ALL = 0x03. First call obtains the required buffer size.
    enum_modules(process, None, 0, ctypes.byref(needed), 0x03)
    if not needed.value:
        return {
            "available": False,
            "error_code": ctypes.get_last_error(),
            "modules": [],
            "input_related_modules": [],
        }

    capacity = max(needed.value // ctypes.sizeof(wintypes.HMODULE) + 16, 64)
    handles = (wintypes.HMODULE * capacity)()
    if not enum_modules(
        process, handles, ctypes.sizeof(handles), ctypes.byref(needed), 0x03
    ):
        return {
            "available": False,
            "error_code": ctypes.get_last_error(),
            "modules": [],
            "input_related_modules": [],
        }

    count = min(capacity, needed.value // ctypes.sizeof(wintypes.HMODULE))
    paths: list[str] = []
    for module in handles[:count]:
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_module_name(process, module, buffer, len(buffer))
        if length:
            paths.append(buffer.value)

    paths = sorted(set(paths), key=str.casefold)
    return {
        "available": True,
        "count": len(paths),
        "modules": paths,
        "input_related_modules": [
            path for path in paths if _INPUT_MODULE.search(os.path.basename(path))
        ],
    }


def inspect_native_process(hwnd: int) -> dict[str, Any]:
    """Inspect process/window metadata for a known top-level HWND."""
    if type(hwnd) is not int or hwnd <= 0:
        raise NativeProcessDiagnosticError("A positive native HWND is required.")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetClientRect.restype = wintypes.BOOL
    kernel32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.IsWow64Process2.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT),
        ctypes.POINTER(wintypes.USHORT),
    ]
    kernel32.IsWow64Process2.restype = wintypes.BOOL

    if not user32.IsWindow(hwnd):
        raise NativeProcessDiagnosticError(f"HWND {hwnd} is not a valid window.")

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG), ("top", wintypes.LONG),
            ("right", wintypes.LONG), ("bottom", wintypes.LONG),
        ]

    title_buffer = ctypes.create_unicode_buffer(1024)
    class_buffer = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
    user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
    window_rect, client_rect = RECT(), RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
        raise NativeProcessDiagnosticError("Could not read the game window rectangle.")
    if not user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
        raise NativeProcessDiagnosticError("Could not read the game client rectangle.")

    process_id = wintypes.DWORD()
    thread_id = kernel32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if not process_id.value:
        raise NativeProcessDiagnosticError("Could not resolve the window's process ID.")

    # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
    process = kernel32.OpenProcess(0x1000 | 0x0400 | 0x0010, False, process_id.value)
    if not process:
        raise NativeProcessDiagnosticError(
            f"OpenProcess failed (WinError {ctypes.get_last_error()}). "
            "Try running the diagnostic at the same integrity level as the game."
        )

    try:
        image_buffer = ctypes.create_unicode_buffer(32768)
        image_length = wintypes.DWORD(len(image_buffer))
        if not kernel32.QueryFullProcessImageNameW(
            process, 0, image_buffer, ctypes.byref(image_length)
        ):
            image_path = None
            image_error = ctypes.get_last_error()
        else:
            image_path = image_buffer.value
            image_error = None

        process_machine, native_machine = wintypes.USHORT(), wintypes.USHORT()
        if kernel32.IsWow64Process2(
            process, ctypes.byref(process_machine), ctypes.byref(native_machine)
        ):
            machine_names = {
                0x014C: "x86", 0x8664: "x64", 0xAA64: "ARM64", 0: "native"
            }
            architecture = {
                "process_machine": machine_names.get(
                    process_machine.value, f"0x{process_machine.value:04X}"
                ),
                "native_machine": machine_names.get(
                    native_machine.value, f"0x{native_machine.value:04X}"
                ),
            }
        else:
            architecture = {
                "available": False,
                "error_code": ctypes.get_last_error(),
            }

        modules = _enumerate_modules(process)
        return {
            "hwnd": hwnd,
            "window_title": title_buffer.value,
            "window_class": class_buffer.value,
            "window_style": f"0x{user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF:08X}",
            "window_ex_style": f"0x{user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF:08X}",
            "window_rect": [
                window_rect.left, window_rect.top,
                window_rect.right, window_rect.bottom,
            ],
            "client_rect": [
                client_rect.left, client_rect.top,
                client_rect.right, client_rect.bottom,
            ],
            "thread_id": thread_id,
            "process_id": process_id.value,
            "executable_path": image_path,
            "executable_path_error": image_error,
            "executable_architecture": _pe_machine(image_path) if image_path else None,
            "process_architecture": architecture,
            "modules": modules,
            "read_only": True,
            "memory_read": False,
            "memory_written": False,
            "code_injected": False,
            "input_sent": False,
            "focus_changed": False,
        }
    finally:
        kernel32.CloseHandle(process)
