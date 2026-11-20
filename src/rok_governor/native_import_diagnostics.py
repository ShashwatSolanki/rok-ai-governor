"""Read-only PE import-table inspection for the native Windows game client.

Files are opened read-only and parsed as data. No executable is loaded, no
process memory is accessed, and no game input is sent.
"""
from __future__ import annotations

import os
import re
import struct
from pathlib import Path
from typing import Any


class NativeImportDiagnosticError(RuntimeError):
    """A PE image could not be safely inspected."""


_INPUT_API = re.compile(
    r"(rawinput|registerrawinput|GetRawInput|xinput|directinput|"
    r"DirectInput|gameinput|sendinput|mouse|keyboard|keybd|"
    r"getasynckeystate|getkeystate|registerhotkey|"
    r"loadlibrary|loadlibraryex|getprocaddress|getmodulehandle)",
    re.IGNORECASE,
)


def _u16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise NativeImportDiagnosticError("Truncated PE structure (16-bit field).")
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise NativeImportDiagnosticError("Truncated PE structure (32-bit field).")
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 8 > len(data):
        raise NativeImportDiagnosticError("Truncated PE structure (64-bit field).")
    return struct.unpack_from("<Q", data, offset)[0]


def _cstring(data: bytes, offset: int, limit: int = 4096) -> str:
    if offset < 0 or offset >= len(data):
        raise NativeImportDiagnosticError("PE string points outside the file.")
    end = data.find(b"\\0", offset, min(len(data), offset + limit))
    if end < 0:
        raise NativeImportDiagnosticError("Unterminated or oversized PE string.")
    return data[offset:end].decode("ascii", errors="replace")


def _parse_pe(data: bytes) -> dict[str, Any]:
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise NativeImportDiagnosticError("Not a valid DOS/PE image.")
    pe_offset = _u32(data, 0x3C)
    if pe_offset > len(data) - 24 or data[pe_offset:pe_offset + 4] != b"PE\\0\\0":
        raise NativeImportDiagnosticError("Missing or out-of-range PE signature.")

    coff = pe_offset + 4
    machine = _u16(data, coff)
    section_count = _u16(data, coff + 2)
    optional_size = _u16(data, coff + 16)
    optional = coff + 20
    if optional_size < 96 or optional + optional_size > len(data):
        raise NativeImportDiagnosticError("Invalid or truncated optional header.")

    magic = _u16(data, optional)
    if magic == 0x20B:  # PE32+
        pointer_size, directory_offset, image_base_offset = 8, 112, 24
    elif magic == 0x10B:  # PE32
        pointer_size, directory_offset, image_base_offset = 4, 96, 28
    else:
        raise NativeImportDiagnosticError(f"Unsupported PE optional-header magic 0x{magic:04X}.")

    if optional_size < directory_offset + 16:
        raise NativeImportDiagnosticError("Optional header does not contain the import directory.")
    image_base = (
        _u64(data, optional + image_base_offset)
        if pointer_size == 8 else _u32(data, optional + image_base_offset)
    )
    size_of_headers = _u32(data, optional + 60)
    number_of_directories = _u32(data, optional + directory_offset - 4)
    import_rva = import_size = 0
    if number_of_directories > 1:
        import_rva = _u32(data, optional + directory_offset + 8)
        import_size = _u32(data, optional + directory_offset + 12)

    section_table = optional + optional_size
    if section_table + section_count * 40 > len(data):
        raise NativeImportDiagnosticError("Truncated PE section table.")
    sections = []
    for index in range(section_count):
        base = section_table + index * 40
        virtual_size = _u32(data, base + 8)
        virtual_address = _u32(data, base + 12)
        raw_size = _u32(data, base + 16)
        raw_pointer = _u32(data, base + 20)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_size, raw_pointer))

    def rva_to_offset(rva: int) -> int:
        if rva < size_of_headers:
            if rva >= len(data):
                raise NativeImportDiagnosticError("Header RVA points outside the file.")
            return rva
        for virtual_address, span, raw_size, raw_pointer in sections:
            delta = rva - virtual_address
            if 0 <= delta < span:
                if delta >= raw_size:
                    raise NativeImportDiagnosticError("RVA refers to unbacked section data.")
                offset = raw_pointer + delta
                if offset >= len(data):
                    raise NativeImportDiagnosticError("RVA maps outside the file.")
                return offset
        raise NativeImportDiagnosticError(f"Could not map PE RVA 0x{rva:X} to file data.")

    imports: dict[str, list[str]] = {}
    symbol_total = 0
    if import_rva and import_size:
        descriptor_offset = rva_to_offset(import_rva)
        descriptor_limit = min(import_size // 20, 4096)
        for descriptor_index in range(descriptor_limit):
            entry = descriptor_offset + descriptor_index * 20
            if entry + 20 > len(data):
                raise NativeImportDiagnosticError("Import descriptor table is truncated.")
            original_thunk = _u32(data, entry)
            name_rva = _u32(data, entry + 12)
            first_thunk = _u32(data, entry + 16)
            if not any(data[entry:entry + 20]):
                break
            if not name_rva:
                continue
            library = _cstring(data, rva_to_offset(name_rva)).lower()
            thunk_rva = original_thunk or first_thunk
            if not thunk_rva:
                imports.setdefault(library, [])
                continue
            thunk_offset = rva_to_offset(thunk_rva)
            names: list[str] = []
            for thunk_index in range(1_000_000):
                item_offset = thunk_offset + thunk_index * pointer_size
                value = _u64(data, item_offset) if pointer_size == 8 else _u32(data, item_offset)
                if value == 0:
                    break
                ordinal_mask = 1 << (pointer_size * 8 - 1)
                if value & ordinal_mask:
                    symbol = f"ordinal_{value & 0xFFFF}"
                else:
                    hint_name_offset = rva_to_offset(value)
                    if hint_name_offset + 2 > len(data):
                        raise NativeImportDiagnosticError("Import-by-name entry is truncated.")
                    symbol = _cstring(data, hint_name_offset + 2)
                names.append(symbol)
                symbol_total += 1
            else:
                raise NativeImportDiagnosticError("Import thunk table exceeded the safety limit.")
            imports.setdefault(library, []).extend(names)
        else:
            raise NativeImportDiagnosticError("Import descriptor table exceeded its safety limit.")

    matched = {
        library: sorted(set(name for name in names if _INPUT_API.search(name)), key=str.casefold)
        for library, names in imports.items()
    }
    matched = {library: names for library, names in matched.items() if names}
    machine_names = {0x014C: "x86", 0x8664: "x64", 0xAA64: "ARM64", 0x01C4: "ARM"}
    return {
        "architecture": machine_names.get(machine, f"unknown (0x{machine:04X})"),
        "import_library_count": len(imports),
        "imported_symbol_count": symbol_total,
        "matched_input_related_imports": matched,
        "dynamic_resolution_indicators": {
            library: [name for name in names if re.search(
                r"(LoadLibrary|GetProcAddress|GetModuleHandle)", name, re.IGNORECASE
            )]
            for library, names in matched.items()
            if any(re.search(r"(LoadLibrary|GetProcAddress|GetModuleHandle)", name, re.IGNORECASE)
                   for name in names)
        },
        "static_import_table_only": True,
    }


def inspect_pe_imports(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Parse normal PE imports from a file without loading or executing it."""
    file_path = Path(path)
    try:
        with file_path.open("rb") as stream:
            data = stream.read()
    except OSError as exc:
        raise NativeImportDiagnosticError(f"Cannot read {file_path}: {exc}") from exc
    result = _parse_pe(data)
    return {
        "path": str(file_path),
        "file_size": len(data),
        **result,
    }


def inspect_game_imports(executable_path: str) -> dict[str, Any]:
    """Inspect the game executable and the adjacent Unity native binaries."""
    executable = Path(executable_path)
    targets = [
        executable,
        executable.with_name("UnityPlayer.dll"),
        executable.with_name("GameAssembly.dll"),
    ]
    reports: list[dict[str, Any]] = []
    for target in targets:
        if not target.is_file():
            reports.append({"path": str(target), "available": False, "reason": "file not found"})
            continue
        try:
            reports.append({"available": True, **inspect_pe_imports(target)})
        except NativeImportDiagnosticError as exc:
            reports.append({"available": False, "path": str(target), "error": str(exc)})
    return {
        "files": reports,
        "read_only": True,
        "process_memory_read": False,
        "process_memory_written": False,
        "code_injected": False,
        "input_sent": False,
        "focus_changed": False,
        "limitations": [
            "Only the on-disk normal import table is inspected; delay-load imports are not included.",
            "Static imports do not prove that an API is used at runtime.",
            "APIs resolved dynamically with GetProcAddress may not appear by their target function name.",
            "A missing match does not prove that the game does not use that input path.",
        ],
    }
