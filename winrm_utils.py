"""WinRM utilities: connectivity check, service queries, file fetching."""
import os
import re
from datetime import datetime
from typing import Optional

try:
    import winrm
    from winrm.exceptions import WinRMTransportError, WinRMOperationTimeoutError
    WINRM_AVAILABLE = True
except ImportError:
    WINRM_AVAILABLE = False


class WinRMError(Exception):
    pass


_SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9_.@() -]+$')


def _sanitize_service_name(name: str) -> str:
    """Validate service name against PowerShell injection."""
    if not name or not _SAFE_NAME_RE.match(name):
        raise WinRMError(f"Invalid service name: {name!r}")
    return name


def _escape_ps_path(path: str) -> str:
    """Escape a file/directory path for safe PowerShell string interpolation."""
    return path.replace("'", "''").replace('"', '`"')


def _get_session(server) -> 'winrm.Session':
    if not WINRM_AVAILABLE:
        raise WinRMError("pywinrm is not installed")
    transport = 'ssl' if server.use_ssl else 'ntlm'
    protocol = 'https' if server.use_ssl else 'http'
    return winrm.Session(
        f'{protocol}://{server.hostname}:{server.port}/wsman',
        auth=(server.credential.username, server.credential.password),
        transport=transport,
        server_cert_validation='ignore',
        operation_timeout_sec=15,
        read_timeout_sec=20,
    )


def test_connection(server) -> tuple[bool, str]:
    """
    Test WinRM connectivity to the server.
    Returns (success: bool, message: str).
    """
    if not WINRM_AVAILABLE:
        return False, "pywinrm not installed — cannot test connection"
    try:
        session = _get_session(server)
        result = session.run_cmd('echo', ['ok'])
        if result.status_code == 0:
            return True, "Connection successful"
        return False, f"Command failed with code {result.status_code}: {result.std_err.decode(errors='replace')}"
    except WinRMTransportError as e:
        return False, f"Transport error: {e}"
    except WinRMOperationTimeoutError:
        return False, "Connection timed out"
    except Exception as e:
        return False, str(e)


def get_service_info(server, win_service_name: str) -> dict:
    """
    Query Windows service details: exe path, display name, status.
    Returns dict with keys: exe_path, display_name, status, error.
    """
    result = {'exe_path': None, 'display_name': None, 'status': 'unknown', 'error': None}
    if not WINRM_AVAILABLE:
        result['error'] = "pywinrm not installed"
        return result
    try:
        safe_name = _sanitize_service_name(win_service_name)
        session = _get_session(server)
        ps = f"""
$svc = Get-WmiObject Win32_Service -Filter "Name='{safe_name}'"
if ($svc) {{
    Write-Output "NAME:$($svc.Name)"
    Write-Output "DISPLAY:$($svc.DisplayName)"
    Write-Output "STATE:$($svc.State)"
    Write-Output "PATH:$($svc.PathName)"
}} else {{
    Write-Output "ERROR:Service not found"
}}
"""
        r = session.run_ps(ps)
        if r.status_code != 0:
            result['error'] = r.std_err.decode(errors='replace')
            return result
        for line in r.std_out.decode(errors='replace').splitlines():
            if line.startswith('DISPLAY:'):
                result['display_name'] = line[8:].strip()
            elif line.startswith('STATE:'):
                result['status'] = line[6:].strip().lower()
            elif line.startswith('PATH:'):
                result['exe_path'] = line[5:].strip().strip('"')
            elif line.startswith('ERROR:'):
                result['error'] = line[6:].strip()
    except Exception as e:
        result['error'] = str(e)
    return result


def infer_config_dir(exe_path: str) -> Optional[str]:
    """
    Auto-detect config directory: <exe_dir>\\config
    """
    if not exe_path:
        return None
    exe_dir = os.path.dirname(exe_path.strip('"'))
    return os.path.join(exe_dir, 'config')


def list_config_files(server, config_dir: str) -> list[dict]:
    """
    List files in config_dir on the remote server.
    Returns list of dicts: {filename, filepath}
    """
    if not WINRM_AVAILABLE:
        return []
    try:
        session = _get_session(server)
        safe_dir = _escape_ps_path(config_dir)
        ps = f"""
$dir = "{safe_dir}"
if (Test-Path $dir) {{
    Get-ChildItem -Path $dir -File | ForEach-Object {{
        Write-Output "$($_.Name)|$($_.FullName)"
    }}
}}
"""
        r = session.run_ps(ps)
        files = []
        if r.status_code == 0:
            for line in r.std_out.decode(errors='replace').splitlines():
                line = line.strip()
                if '|' in line:
                    name, path = line.split('|', 1)
                    files.append({'filename': name.strip(), 'filepath': path.strip()})
        return files
    except Exception:
        return []


def fetch_file_content(server, filepath: str) -> tuple[Optional[str], str]:
    """
    Read a text file from the remote server.
    Reads raw bytes via base64 to avoid PowerShell encoding issues,
    then auto-detects encoding (utf-8-sig → utf-8 → cp1251).
    Returns (content, encoding).
    """
    import base64
    if not WINRM_AVAILABLE:
        return None, 'utf-8'
    try:
        session = _get_session(server)
        # Read as raw bytes via base64 – works regardless of file encoding
        safe_path = _escape_ps_path(filepath)
        ps = f'[Convert]::ToBase64String([IO.File]::ReadAllBytes("{safe_path}"))'
        r = session.run_ps(ps)
        if r.status_code != 0:
            return None, 'utf-8'
        raw = base64.b64decode(r.std_out.strip())
        for enc in ('utf-8-sig', 'utf-8', 'cp1251'):
            try:
                return raw.decode(enc), enc
            except UnicodeDecodeError:
                continue
        # Fallback: cp1251 with replacement characters
        return raw.decode('cp1251', errors='replace'), 'cp1251'
    except Exception:
        return None, 'utf-8'


def fetch_all_configs(server, config_dir: str) -> list[dict]:
    """
    Fetch all config files from config_dir.
    Returns list of dicts: {filename, filepath, content, encoding, fetched_at}
    """
    files = list_config_files(server, config_dir)
    results = []
    for f in files:
        content, encoding = fetch_file_content(server, f['filepath'])
        results.append({
            'filename': f['filename'],
            'filepath': f['filepath'],
            'content': content or '',
            'encoding': encoding,
            'fetched_at': datetime.utcnow(),
        })
    return results


def write_file_content(server, filepath: str, content: str, encoding: str = 'utf-8') -> tuple[bool, str]:
    """
    Write text content to a file on the remote server via WinRM.
    Uses base64 to avoid PowerShell encoding issues.
    Returns (success, message).
    """
    import base64
    if not WINRM_AVAILABLE:
        return False, "pywinrm not installed"
    try:
        session = _get_session(server)
        raw = content.encode(encoding, errors='replace')
        b64 = base64.b64encode(raw).decode('ascii')
        safe_path = _escape_ps_path(filepath)
        ps = f"""$bytes = [System.Convert]::FromBase64String('{b64}')
[System.IO.File]::WriteAllBytes("{safe_path}", $bytes)
"""
        r = session.run_ps(ps)
        if r.status_code == 0:
            return True, f"Записано {len(raw)} байт"
        err = r.std_err.decode(errors='replace').strip()
        return False, err or f"Exit code {r.status_code}"
    except Exception as e:
        return False, str(e)


def list_services(server) -> tuple[list[dict], Optional[str]]:
    """
    Enumerate all Windows services on the server.
    Returns (services, error_message).
    services — list of {name, display_name, status}
    """
    if not WINRM_AVAILABLE:
        return [], "pywinrm not installed"
    try:
        session = _get_session(server)
        ps = """
Get-WmiObject Win32_Service | Sort-Object Name | ForEach-Object {
    Write-Output "$($_.Name)|$($_.DisplayName)|$($_.State)"
}
"""
        r = session.run_ps(ps)
        if r.status_code != 0:
            return [], r.std_err.decode(errors='replace').strip()
        services = []
        for line in r.std_out.decode(errors='replace').splitlines():
            line = line.strip()
            if not line or '|' not in line:
                continue
            parts = line.split('|', 2)
            services.append({
                'name':         parts[0].strip(),
                'display_name': parts[1].strip() if len(parts) > 1 else '',
                'status':       parts[2].strip().lower() if len(parts) > 2 else 'unknown',
            })
        return services, None
    except Exception as e:
        return [], str(e)


def get_service_status(server, win_service_name: str) -> str:
    """Quick status poll for a single service. Returns lowercase state string."""
    if not WINRM_AVAILABLE:
        return 'unknown'
    try:
        session = _get_session(server)
        safe_name = _sanitize_service_name(win_service_name)
        ps = f"""
$s = Get-Service -Name '{safe_name}' -ErrorAction SilentlyContinue
if ($s) {{ Write-Output $s.Status }} else {{ Write-Output 'NotFound' }}
"""
        r = session.run_ps(ps)
        if r.status_code == 0:
            return r.std_out.decode(errors='replace').strip().lower()
    except Exception:
        pass
    return 'unknown'


def control_service(server, win_service_name: str, action: str) -> tuple[bool, str]:
    """
    Start, stop, or restart a Windows service via WinRM.
    action: 'start' | 'stop' | 'restart'
    Returns (success, message).
    """
    if not WINRM_AVAILABLE:
        return False, "pywinrm not installed"
    action = action.lower()
    safe_name = _sanitize_service_name(win_service_name)
    ps_cmd = {
        'start':   f"Start-Service -Name '{safe_name}' -ErrorAction Stop",
        'stop':    f"Stop-Service  -Name '{safe_name}' -Force -ErrorAction Stop",
        'restart': f"Restart-Service -Name '{safe_name}' -Force -ErrorAction Stop",
    }.get(action)
    if not ps_cmd:
        return False, f"Unknown action: {action}"
    try:
        session = _get_session(server)
        r = session.run_ps(ps_cmd)
        if r.status_code == 0:
            return True, f"Service {action}ed successfully"
        err = r.std_err.decode(errors='replace').strip()
        return False, err or f"Command exited with code {r.status_code}"
    except Exception as e:
        return False, str(e)
