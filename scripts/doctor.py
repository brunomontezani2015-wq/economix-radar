"""Economix Radar - verificacao do ambiente (doctor).

Checa ferramentas, dispositivo Android via ADB e, opcionalmente, uma sessao Appium real.
Uso:  .venv\\Scripts\\python.exe scripts\\doctor.py [--appium]
Saida: codigo 0 se tudo OK, 1 se algo falhou. Nada e inventado: cada linha reflete um comando executado.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SDK = Path(os.environ.get("ANDROID_HOME", r"C:\Android\Sdk"))
ADB = str(SDK / "platform-tools" / "adb.exe")
ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "data" / "evidence" / "_setup"

results: list[tuple[str, bool, str]] = []


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def tools() -> None:
    for name, cmd in {
        "python": [sys.executable, "--version"],
        "node": ["node", "--version"],
        "java": [str(Path(os.environ.get("JAVA_HOME", r"C:\Android\jdk17")) / "bin" / "java.exe"), "-version"],
        "adb": [ADB, "version"],
        "appium": [shutil.which("appium") or "appium", "--version"],
    }.items():
        code, out = run(cmd)
        check(f"tool:{name}", code == 0, out.splitlines()[0] if out else "")


def device() -> str | None:
    code, out = run([ADB, "devices"])
    serials = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
    check("adb:device", bool(serials), ", ".join(serials) or "nenhum dispositivo")
    if not serials:
        return None
    s = serials[0]
    _, rel = run([ADB, "-s", s, "shell", "getprop", "ro.build.version.release"])
    _, boot = run([ADB, "-s", s, "shell", "getprop", "sys.boot_completed"])
    check("adb:boot", boot.strip() == "1", f"Android {rel}")
    EVID.mkdir(parents=True, exist_ok=True)
    png = EVID / "doctor_screencap.png"
    with open(png, "wb") as f:
        p = subprocess.run([ADB, "-s", s, "exec-out", "screencap", "-p"], stdout=f, timeout=30)
    check("adb:screencap", p.returncode == 0 and png.stat().st_size > 10_000, f"{png.stat().st_size} bytes")
    code, out = run([ADB, "-s", s, "shell", "uiautomator", "dump", "/sdcard/ui.xml"])
    check("adb:uiautomator", "dumped" in out, out)
    return s


def appium(serial: str) -> None:
    from appium import webdriver
    from appium.options.android import UiAutomator2Options

    log = open(EVID / "appium.log", "w", encoding="utf-8")
    srv = subprocess.Popen([shutil.which("appium") or "appium", "--port", "4723"],
                           stdout=log, stderr=subprocess.STDOUT, shell=False)
    try:
        import httpx
        for _ in range(30):
            try:
                if httpx.get("http://127.0.0.1:4723/status", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(1)
        opts = UiAutomator2Options()
        opts.udid = serial
        opts.no_reset = True
        opts.new_command_timeout = 60
        drv = webdriver.Remote("http://127.0.0.1:4723", options=opts)
        try:
            src = drv.page_source
            drv.get_screenshot_as_file(str(EVID / "appium_screenshot.png"))
            check("appium:session", len(src) > 500, f"page_source {len(src)} chars, pkg={drv.current_package}")
        finally:
            drv.quit()
    except Exception as e:  # noqa: BLE001 - relatorio de diagnostico
        check("appium:session", False, repr(e)[:300])
    finally:
        srv.terminate()
        log.close()


def main() -> int:
    tools()
    serial = device()
    if serial and "--appium" in sys.argv:
        appium(serial)
    for name, ok, detail in results:
        print(f"[{'OK ' if ok else 'FALHA'}] {name:18} {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
