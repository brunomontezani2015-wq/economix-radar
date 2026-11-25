"""Camada de dispositivo Android via ADB puro (rapido, deterministico, sem servidor).

Appium fica reservado para fluxos que exigem esperas/gestos complexos.
A arvore de UI e reduzida a uma lista compacta de nos com texto, para economizar tokens
quando a IA precisa "ler" uma tela durante a exploracao.
"""
from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .config import settings

_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class DeviceError(RuntimeError):
    pass


@dataclass
class UiNode:
    text: str
    desc: str
    rid: str
    cls: str
    clickable: bool
    scrollable: bool
    bounds: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def label(self) -> str:
        return self.text or self.desc


class Device:
    def __init__(self, serial: str | None = None):
        self.serial = serial or settings.device_serial or self._first_device()

    # ---- baixo nivel -------------------------------------------------
    def _adb(self, *args: str, timeout: int = 30, binary: bool = False):
        cmd = [settings.adb, "-s", self.serial, *args]
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if p.returncode != 0:
            raise DeviceError(f"{' '.join(args)} -> {p.stderr.decode(errors='replace').strip()}")
        return p.stdout if binary else p.stdout.decode("utf-8", errors="replace")

    @staticmethod
    def _first_device() -> str:
        out = subprocess.run([settings.adb, "devices"], capture_output=True, text=True).stdout
        serials = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
        if not serials:
            raise DeviceError("Nenhum dispositivo Android conectado (adb devices vazio).")
        return serials[0]

    def shell(self, *args: str, timeout: int = 30) -> str:
        return self._adb("shell", *args, timeout=timeout)

    # ---- estado ------------------------------------------------------
    def is_installed(self, package: str) -> bool:
        return f"package:{package}" in self.shell("pm", "list", "packages", package).split()

    def packages(self, third_party: bool = True) -> list[str]:
        out = self.shell("pm", "list", "packages", *(["-3"] if third_party else []))
        return sorted(l.removeprefix("package:").strip() for l in out.splitlines() if l.strip())

    def current_package(self) -> str | None:
        out = self.shell("dumpsys", "window", "displays")
        m = re.search(r"mCurrentFocus=.*\s([\w.]+)/", out) or re.search(r"mFocusedApp=.*\s([\w.]+)/", out)
        return m.group(1) if m else None

    # ---- acoes -------------------------------------------------------
    def launch(self, package: str, wait: float = 4.0) -> None:
        self.shell("monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(wait)

    def stop(self, package: str) -> None:
        self.shell("am", "force-stop", package)

    def tap(self, x: int, y: int, wait: float = 1.5) -> None:
        self.shell("input", "tap", str(x), str(y))
        time.sleep(wait)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 400, wait: float = 1.2) -> None:
        self.shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))
        time.sleep(wait)

    def scroll_down(self, fraction: float = 0.55) -> None:
        w, h = self.screen_size()
        self.swipe(w // 2, int(h * 0.75), w // 2, int(h * (0.75 - fraction)))

    def back(self, wait: float = 1.0) -> None:
        self.shell("input", "keyevent", "KEYCODE_BACK")
        time.sleep(wait)

    def screen_size(self) -> tuple[int, int]:
        m = re.search(r"(\d+)x(\d+)", self.shell("wm", "size"))
        if not m:
            raise DeviceError("Nao foi possivel ler o tamanho da tela.")
        return int(m.group(1)), int(m.group(2))

    # ---- captura -----------------------------------------------------
    def screenshot(self) -> bytes:
        data = self._adb("exec-out", "screencap", "-p", binary=True)
        if not data.startswith(b"\x89PNG"):
            raise DeviceError("screencap nao retornou PNG valido.")
        return data

    def ui_xml(self, retries: int = 3) -> str:
        last = ""
        for _ in range(retries):
            last = self.shell("uiautomator", "dump", "/sdcard/economix_ui.xml")
            if "dumped" in last:
                return self.shell("cat", "/sdcard/economix_ui.xml")
            time.sleep(1)  # tela animando: tenta de novo
        raise DeviceError(f"uiautomator dump falhou: {last}")

    def ui_nodes(self) -> list[UiNode]:
        return parse_nodes(self.ui_xml())

    def tap_text(self, text: str, exact: bool = False) -> bool:
        for n in self.ui_nodes():
            lbl = n.label
            if (lbl == text) if exact else (text.lower() in lbl.lower()):
                self.tap(*n.center)
                return True
        return False


def parse_nodes(xml: str) -> list[UiNode]:
    root = ET.fromstring(xml)
    nodes = []
    for el in root.iter("node"):
        m = _BOUNDS.match(el.get("bounds", ""))
        if not m:
            continue
        nodes.append(UiNode(
            text=(el.get("text") or "").strip(),
            desc=(el.get("content-desc") or "").strip(),
            rid=(el.get("resource-id") or "").split("/")[-1],
            cls=(el.get("class") or "").split(".")[-1],
            clickable=el.get("clickable") == "true",
            scrollable=el.get("scrollable") == "true",
            bounds=tuple(int(g) for g in m.groups()),  # type: ignore[arg-type]
        ))
    return nodes


def compact_tree(nodes: list[UiNode]) -> str:
    """Representacao enxuta (1 linha por no relevante) para a IA ler com poucos tokens."""
    lines = []
    for i, n in enumerate(nodes):
        if not (n.label or n.clickable or n.scrollable):
            continue
        flags = ("C" if n.clickable else "") + ("S" if n.scrollable else "")
        x, y = n.center
        lines.append(f"{i}|{flags}|{n.cls}|{n.rid}|{x},{y}|{n.label[:80]}")
    return "\n".join(lines)
