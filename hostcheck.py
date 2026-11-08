"""Host readiness for AnyPS5, checked against upstream's documented requirements
(boykopovar/AnyPS5 docs/user/USAGE.md, re-read 2026-11-07).

These checks say whether this PC meets what AnyPS5 documents as required. They never say
that a particular game runs; per-game compatibility stays the user's own note.
"""
from __future__ import annotations
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

# "the host needs AVX2, FMA, F16C, BMI1, BMI2, LZCNT, and MOVBE" (among others).
# /proc/cpuinfo calls LZCNT "abm" on Intel CPUs and AMD CPUs before Zen.
CPU_FEATURES = (('AVX2', ('avx2',)), ('FMA', ('fma',)), ('F16C', ('f16c',)), ('BMI1', ('bmi1',)),
                ('BMI2', ('bmi2',)), ('LZCNT', ('lzcnt', 'abm')), ('MOVBE', ('movbe',)))
VULKAN_EXTENSIONS = ('VK_KHR_8bit_storage', 'VK_KHR_buffer_device_address', 'VK_KHR_shader_float_controls', 'VK_KHR_swapchain')
VULKAN_FEATURES = ('bufferDeviceAddress', 'shaderInt64', 'textureCompressionBC')
NVIDIA_PROBLEM_DRIVERS = {'610.47': 'NVIDIA driver 610.47 can crash in its shader compiler while pipelines are created; upstream cites 617.42 as working'}


def _check(cid, label, status, detail):
    return {'id': cid, 'label': label, 'status': status, 'detail': detail}


def cpu_flags(cpuinfo_text):
    for line in cpuinfo_text.splitlines():
        key, _, value = line.partition(':')
        if key.strip() == 'flags':
            return set(value.split())
    return set()


def check_cpu(cpuinfo_path='/proc/cpuinfo', machine=None):
    machine = machine or platform.machine()
    checks = [_check('arch', 'x86-64 host', 'pass' if machine in ('x86_64', 'AMD64') else 'fail', machine)]
    try:
        flags = cpu_flags(Path(cpuinfo_path).read_text(encoding='utf-8', errors='replace'))
    except OSError as exc:
        return checks + [_check('cpu', 'CPU instruction sets', 'unknown', f'could not read {cpuinfo_path}: {exc}')]
    missing = [name for name, aliases in CPU_FEATURES if not any(a in flags for a in aliases)]
    checks.append(_check('cpu', 'CPU instruction sets (AVX2, FMA, F16C, BMI1, BMI2, LZCNT, MOVBE)',
                         'fail' if missing else 'pass', 'missing: ' + ', '.join(missing) if missing else 'all present'))
    return checks


def check_udmabuf(path='/dev/udmabuf'):
    p = Path(path)
    if not p.exists():
        return _check('udmabuf', '/dev/udmabuf read-write access', 'fail',
                      f'{path} does not exist (the udmabuf kernel module is not loaded: sudo modprobe udmabuf)')
    if os.access(p, os.R_OK | os.W_OK):
        return _check('udmabuf', '/dev/udmabuf read-write access', 'pass', 'available to this user')
    return _check('udmabuf', '/dev/udmabuf read-write access', 'fail',
                  f'{path} exists but this user cannot read and write it (games print "[gpu] open /dev/udmabuf: Permission denied")')


def parse_vulkan_full(text):
    """From full `vulkaninfo` output: highest device apiVersion, extensions and feature flags seen."""
    versions = [tuple(int(x) for x in m.groups()) for m in re.finditer(r'apiVersion\s*=\s*(?:\d+\s*\()?(\d+)\.(\d+)\.(\d+)', text)]
    extensions = set(re.findall(r'\b(VK_[A-Z0-9_]+[a-z0-9_]*)\b\s*:\s*extension revision', text))
    features = {}
    for name in VULKAN_FEATURES:
        values = re.findall(r'\b' + name + r'\s*=\s*(true|false)', text)
        if values:
            features[name] = 'true' in values
    return (max(versions) if versions else None), extensions, features


def check_vulkan(run=None):
    run = run or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=20,
                                              stdin=subprocess.DEVNULL, errors='replace'))
    if not shutil.which('vulkaninfo'):
        return [_check('vulkan', 'Vulkan 1.1 device', 'unknown', 'vulkaninfo is not installed (sudo apt install vulkan-tools)')]
    try:
        cp = run(['vulkaninfo'])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [_check('vulkan', 'Vulkan 1.1 device', 'unknown', f'vulkaninfo failed: {exc}')]
    version, extensions, features = parse_vulkan_full(cp.stdout or '')
    checks = []
    if version is None:
        checks.append(_check('vulkan', 'Vulkan 1.1 device', 'fail', 'no Vulkan device reported'))
        return checks
    vtext = '.'.join(map(str, version))
    checks.append(_check('vulkan', 'Vulkan 1.1 device', 'pass' if version >= (1, 1, 0) else 'fail', f'highest apiVersion {vtext}'))
    missing_ext = [e for e in VULKAN_EXTENSIONS if e not in extensions]
    # Extensions promoted to core in newer Vulkan versions may not be listed; report, do not fail.
    checks.append(_check('vk_ext', 'Required Vulkan extensions', 'pass' if not missing_ext else 'warn',
                         'all listed' if not missing_ext else 'not listed by vulkaninfo: ' + ', '.join(missing_ext)))
    missing_feat = [f for f in VULKAN_FEATURES if features.get(f) is False]
    unknown_feat = [f for f in VULKAN_FEATURES if f not in features]
    status = 'fail' if missing_feat else ('warn' if unknown_feat else 'pass')
    detail = ('unsupported: ' + ', '.join(missing_feat)) if missing_feat else (('not reported: ' + ', '.join(unknown_feat)) if unknown_feat else 'all supported')
    checks.append(_check('vk_feat', 'Required Vulkan features (bufferDeviceAddress, shaderInt64, textureCompressionBC)', status, detail))
    return checks


def check_nvidia_driver(run=None):
    run = run or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=10,
                                              stdin=subprocess.DEVNULL, errors='replace'))
    if not shutil.which('nvidia-smi'):
        return _check('nvidia', 'NVIDIA driver', 'unknown', 'nvidia-smi not found (not an NVIDIA system, or driver tools not installed)')
    try:
        cp = run(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _check('nvidia', 'NVIDIA driver', 'unknown', str(exc))
    version = (cp.stdout or '').strip().splitlines()[0].strip() if (cp.stdout or '').strip() else ''
    if not version:
        return _check('nvidia', 'NVIDIA driver', 'unknown', 'nvidia-smi reported no driver version')
    if version in NVIDIA_PROBLEM_DRIVERS:
        return _check('nvidia', 'NVIDIA driver', 'warn', f'{version}: {NVIDIA_PROBLEM_DRIVERS[version]}')
    return _check('nvidia', 'NVIDIA driver', 'pass', f'{version} (not a driver upstream reports as crashing)')


def readiness(cpuinfo_path='/proc/cpuinfo', udmabuf_path='/dev/udmabuf'):
    checks = check_cpu(cpuinfo_path) + [check_udmabuf(udmabuf_path)] + check_vulkan() + [check_nvidia_driver()]
    summary = 'fail' if any(c['status'] == 'fail' for c in checks) else ('warn' if any(c['status'] in ('warn', 'unknown') for c in checks) else 'pass')
    return {'summary': summary, 'checks': checks,
            'note': 'Checks follow AnyPS5\'s documented host requirements. Passing them does not mean a particular game runs.'}
