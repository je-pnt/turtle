"""
Restart Manager - Provides platform-specific utilities for restarting USB devices at the OS level.
This module enables hardwareService and related components to perform low-level USB resets and recoveries, supporting device reinitialization and robust error handling. It abstracts OS-specific commands for Windows, Linux, and macOS, allowing the system to trigger USB restarts programmatically without external dependencies.

Key features:
- Implements async USB restart routines for major operating systems
- Self-contained, no third-party dependencies

Property of Uncompromising Sensors LLC.
"""


# Imports
import asyncio, subprocess, platform
from sdk.logging import getLogger

# Module-level logger (auto-detects: 'hardwareService.restartManager')
log = getLogger()


# Functions
async def restartUsb(deviceId: str) -> dict:
    """Best-effort USB restart - platform-specific, self-contained."""
    system = platform.system().lower()
    if system == "windows": return await _restartUsbWindows()
    if system == "linux": return await _restartUsbLinux()
    if system == "darwin": return await _restartUsbMacos()
    return {"ok": False, "error": f"unsupported platform: {system}"}


async def _restartUsbWindows():
    """Windows USB restart - requires administrator privileges."""
    try:
        psCmd = r'''$hubs = Get-PnpDevice -PresentOnly | Where-Object { $_.Class -eq 'USB' -and ($_.FriendlyName -match 'Root Hub' -or $_.FriendlyName -match 'Generic USB Hub' -or $_.FriendlyName -match 'USB 3.0 Hub' -or $_.FriendlyName -match 'USB4 Hub') }; foreach ($d in $hubs) { Write-Host ('Restarting: ' + $d.FriendlyName); Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 500; Enable-PnpDevice  -InstanceId $d.InstanceId -Confirm:$false -ErrorAction SilentlyContinue }'''
        log.info("Restarting USB hubs (requires admin privileges)")
        proc = await asyncio.create_subprocess_exec("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", psCmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        stdoutText, stderrText = stdout.decode('utf-8', errors='ignore').strip(), stderr.decode('utf-8', errors='ignore').strip()
        if stdoutText: 
            log.debug("PowerShell output", output=stdoutText)
        if stderrText:
            if any(x in stderrText for x in ["NotSupported", "Access is denied", "Administrator"]):
                errorMsg = "USB restart requires administrator privileges. Run hardwareService as administrator."
                log.error(errorMsg)
                return {"ok": False, "error": errorMsg}
            elif any(x in stderrText.lower() for x in ["error", "exception"]): 
                log.warning("PowerShell stderr", stderr=stderrText)
        if proc.returncode == 0:
            log.info("USB hub restart completed")
            return {"ok": True, "error": None}
        errorMsg = f"USB restart failed (exit code {proc.returncode}). Try running as administrator."
        log.error(errorMsg, exitCode=proc.returncode)
        return {"ok": False, "error": errorMsg}
    except Exception as e:
        errorMsg = f"USB restart exception: {str(e)}"
        log.error(errorMsg, exception=str(e))
        return {"ok": False, "error": errorMsg}


async def _restartUsbLinux():
    """Linux USB restart - unbind/bind USB hub controllers to force device re-enumeration."""
    try:
        import glob
        import os
        
        log.info("Starting USB hub reset (Linux)")
        
        # Find all USB host controllers (EHCI, XHCI, OHCI, UHCI)
        hub_paths = []
        for pattern in ['/sys/bus/usb/drivers/usb/*usb*', '/sys/bus/pci/drivers/xhci_hcd/*:*', 
                       '/sys/bus/pci/drivers/ehci-pci/*:*', '/sys/bus/pci/drivers/ohci-pci/*:*']:
            hub_paths.extend(glob.glob(pattern))
        
        if not hub_paths:
            log.warning("No USB hubs found to reset")
            return {"ok": False, "error": "No USB hubs found"}
        
        log.info(f"Found {len(hub_paths)} USB controller(s) to reset")
        
        # Unbind all USB controllers (releases all USB devices)
        unbound = []
        for hub_path in hub_paths:
            try:
                if not os.path.islink(hub_path):
                    continue
                    
                controller_id = os.path.basename(hub_path)
                driver_path = os.path.dirname(hub_path)
                unbind_path = os.path.join(driver_path, 'unbind')
                
                # Write controller ID to unbind file
                proc = await asyncio.create_subprocess_exec(
                    'sudo', 'sh', '-c', f'echo {controller_id} > {unbind_path}',
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                
                if proc.returncode == 0:
                    unbound.append((controller_id, driver_path))
                    log.debug(f"Unbound USB controller", controller=controller_id)
                else:
                    stderr_text = stderr.decode('utf-8', errors='ignore').strip()
                    log.warning(f"Failed to unbind controller", controller=controller_id, stderr=stderr_text)
                    
            except Exception as e:
                log.warning(f"Error unbinding controller", path=hub_path, error=str(e))
        
        if not unbound:
            return {"ok": False, "error": "Failed to unbind any USB controllers"}
        
        # Wait for USB bus to settle
        await asyncio.sleep(1.0)
        
        # Rebind all USB controllers (triggers device re-enumeration)
        rebound = 0
        for controller_id, driver_path in unbound:
            try:
                bind_path = os.path.join(driver_path, 'bind')
                
                proc = await asyncio.create_subprocess_exec(
                    'sudo', 'sh', '-c', f'echo {controller_id} > {bind_path}',
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                await proc.communicate()
                
                if proc.returncode == 0:
                    rebound += 1
                    log.debug(f"Rebound USB controller", controller=controller_id)
                    
            except Exception as e:
                log.warning(f"Error rebinding controller", controller=controller_id, error=str(e))
        
        if rebound > 0:
            log.info(f"USB reset completed (Linux)", controllersReset=rebound)
            return {"ok": True, "error": None}
        else:
            return {"ok": False, "error": "Failed to rebind USB controllers"}
            
    except Exception as e:
        log.error("USB restart failed (Linux)", exception=str(e))
        return {"ok": False, "error": str(e)}


async def _restartUsbMacos():
    """macOS USB restart - limited support."""
    try:
        proc = await asyncio.create_subprocess_exec("sudo", "kextunload", "-b", "com.apple.iokit.IOUSBFamily", stdout=subprocess.PIPE, stderr=subprocess.PIPE); await proc.communicate()
        await asyncio.sleep(0.5)
        proc2 = await asyncio.create_subprocess_exec("sudo", "kextload", "-b", "com.apple.iokit.IOUSBFamily", stdout=subprocess.PIPE, stderr=subprocess.PIPE); await proc2.communicate()
        log.info("USB restart completed (macOS)")
        return {"ok": True, "error": None}
    except Exception as e:
        errorMsg = f"macos usb restart not reliable: {str(e)}"
        log.warning(errorMsg, exception=str(e))
        return {"ok": False, "error": errorMsg}