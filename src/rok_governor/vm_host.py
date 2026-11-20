"""Read-only host readiness checks for running the official RoK client in a VM."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any


def diagnose_vm_host() -> dict[str, Any]:
    """Collect host CPU, memory, GPU, disk, and virtualization information."""
    if os.name != "nt":
        raise RuntimeError("VM host diagnostics currently support Windows hosts only.")

    powershell = r"""
$ErrorActionPreference = 'Stop'
$cs = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
$cpus = @(Get-CimInstance Win32_Processor | ForEach-Object {
    [pscustomobject]@{
        Name = $_.Name
        Cores = $_.NumberOfCores
        LogicalProcessors = $_.NumberOfLogicalProcessors
        VirtualizationFirmwareEnabled = $_.VirtualizationFirmwareEnabled
        VMMonitorModeExtensions = $_.VMMonitorModeExtensions
    }
})
$gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object {
    [pscustomobject]@{
        Name = $_.Name
        AdapterRAM = $_.AdapterRAM
        DriverVersion = $_.DriverVersion
        DriverDate = $_.DriverDate
    }
})
$systemDisk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
[pscustomobject]@{
    ComputerManufacturer = $cs.Manufacturer
    ComputerModel = $cs.Model
    TotalPhysicalMemory = $cs.TotalPhysicalMemory
    HypervisorPresent = $cs.HypervisorPresent
    OSName = $os.Caption
    OSVersion = $os.Version
    OSBuild = $os.BuildNumber
    OSArchitecture = $os.OSArchitecture
    CPUs = $cpus
    GPUs = $gpus
    SystemDriveFreeBytes = $systemDisk.FreeSpace
    SystemDriveSizeBytes = $systemDisk.Size
} | ConvertTo-Json -Depth 5 -Compress
"""
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if not executable:
        raise RuntimeError("Windows PowerShell was not found; no host changes were made.")

    try:
        completed = subprocess.run(
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", powershell],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Windows host query timed out after 20 seconds.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"PowerShell host query failed: {detail or completed.returncode}")

    try:
        raw = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("PowerShell returned invalid host-diagnostic JSON.") from exc

    cpus = raw.get("CPUs") or []
    gpus = raw.get("GPUs") or []
    if isinstance(cpus, dict):
        cpus = [cpus]
    if isinstance(gpus, dict):
        gpus = [gpus]

    ram_gb = round(int(raw.get("TotalPhysicalMemory") or 0) / (1024 ** 3), 1)
    cores = sum(int(cpu.get("Cores") or 0) for cpu in cpus)
    logical_processors = sum(int(cpu.get("LogicalProcessors") or 0) for cpu in cpus)
    free_gb = round(int(raw.get("SystemDriveFreeBytes") or 0) / (1024 ** 3), 1)
    disk_gb = round(int(raw.get("SystemDriveSizeBytes") or 0) / (1024 ** 3), 1)

    if ram_gb >= 24:
        guest_ram_gb = 8
    elif ram_gb >= 16:
        guest_ram_gb = 6
    elif ram_gb >= 12:
        guest_ram_gb = 4
    else:
        guest_ram_gb = None

    guest_vcpus = 4 if cores >= 8 else (2 if cores >= 4 else None)
    firmware_virtualization = all(
        bool(cpu.get("VirtualizationFirmwareEnabled")) for cpu in cpus
    ) if cpus else False

    recommendations = []
    if not firmware_virtualization:
        recommendations.append(
            "CPU virtualization is not reported as enabled in firmware. Check BIOS/UEFI for Intel VT-x or AMD SVM; this diagnostic does not change firmware settings."
        )
    if ram_gb < 12:
        recommendations.append(
            "Host RAM is below 12 GB. A Windows guest plus the game may leave too little memory for comfortable multitasking."
        )
    if free_gb < 80:
        recommendations.append(
            "Less than 80 GB is free on C:. Leave room for a Windows guest, game files, updates, and VM snapshots."
        )
    if guest_ram_gb is not None and guest_vcpus is not None:
        recommendations.append(
            f"Initial conservative VM allocation to try: {guest_ram_gb} GB RAM and {guest_vcpus} virtual CPUs. Keep the VM on a virtual display with 3D acceleration enabled."
        )
    else:
        recommendations.append(
            "Wait for the host resource report before choosing VM CPU and memory allocation."
        )
    if raw.get("HypervisorPresent"):
        recommendations.append(
            "A Windows hypervisor is currently present. VMware may use the Windows Hypervisor Platform compatibility path; performance depends on the installed Windows and VMware versions."
        )

    return {
        "host": {
            "manufacturer": raw.get("ComputerManufacturer"),
            "model": raw.get("ComputerModel"),
            "os": raw.get("OSName"),
            "os_version": raw.get("OSVersion"),
            "os_build": raw.get("OSBuild"),
            "architecture": raw.get("OSArchitecture"),
        },
        "cpu": {
            "models": [cpu.get("Name") for cpu in cpus if cpu.get("Name")],
            "physical_cores": cores,
            "logical_processors": logical_processors,
            "firmware_virtualization_enabled": firmware_virtualization,
            "hypervisor_present": bool(raw.get("HypervisorPresent")),
        },
        "memory": {"total_gb": ram_gb, "suggested_guest_gb": guest_ram_gb},
        "graphics": [
            {
                "name": gpu.get("Name"),
                "adapter_memory_gb": (
                    round(int(gpu["AdapterRAM"]) / (1024 ** 3), 2)
                    if gpu.get("AdapterRAM") else None
                ),
                "driver_version": gpu.get("DriverVersion"),
                "driver_date": gpu.get("DriverDate"),
            }
            for gpu in gpus
        ],
        "system_drive": {
            "free_gb": free_gb,
            "total_gb": disk_gb,
            "suggested_guest_vcpus": guest_vcpus,
        },
        "recommendations": recommendations,
        "read_only": True,
    }
