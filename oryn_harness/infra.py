"""Infrastructure manager — lance et vérifie les services avant les tests.

Gère le cycle de vie complet :
- Émulateur Android (boot, wait, health check)
- Simulateur iOS (boot, wait)
- Convex local dev (npx convex dev)
- Serveur web (pnpm dev)
- Health checks avec retries
- Cleanup propre

Chaque service a un pattern : start → wait_ready → health_check → evidence.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

console = Console()

# Default port, overridden by detection
DEFAULT_WEB_PORT = 3000


@dataclass
class ServiceStatus:
    """État d'un service après lancement."""

    name: str
    running: bool = False
    healthy: bool = False
    logs: str = ""
    errors: list[str] = field(default_factory=list)
    pid: int | None = None
    url: str | None = None
    port: int | None = None


def get_web_base_url(workdir: Path) -> str:
    """Lit le base URL web depuis .oryn/infra.json (écrit par InfraManager)."""
    infra_path = workdir / ".oryn" / "infra.json"
    if infra_path.exists():
        try:
            data = json.loads(infra_path.read_text())
            return data.get("web_url", f"http://localhost:{DEFAULT_WEB_PORT}")
        except (json.JSONDecodeError, OSError):
            pass
    return f"http://localhost:{DEFAULT_WEB_PORT}"


class InfraManager:
    """Gère tous les services nécessaires pour tester les apps."""

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self._procs: list[subprocess.Popen] = []
        self._services: dict[str, ServiceStatus] = {}
        self._web_port: int = DEFAULT_WEB_PORT

    @property
    def web_base_url(self) -> str:
        return f"http://localhost:{self._web_port}"

    def start_all(self) -> dict[str, ServiceStatus]:
        """Lance tous les services détectés et retourne leur état."""
        console.rule("[bold blue]INFRASTRUCTURE")

        # 1. Convex (backend) — DOIT être up avant le reste
        if self._has_convex():
            self._services["convex"] = self._start_convex()

        # 2. Web server
        if (self.workdir / "apps" / "web").exists():
            self._services["web"] = self._start_web()

        # 3. Android emulator
        if (self.workdir / "apps" / "mobile").exists():
            self._services["android"] = self._start_android_emulator()

        # 4. Expo mobile
        if (self.workdir / "apps" / "mobile").exists() and self._services.get("android", ServiceStatus(name="android")).running:
            self._services["expo"] = self._start_expo()

        # Résumé
        console.print("")
        for name, svc in self._services.items():
            icon = "[green]✓[/green]" if svc.healthy else ("[yellow]~[/yellow]" if svc.running else "[red]✗[/red]")
            console.print(f"  {icon} {name}: {'healthy' if svc.healthy else ('running' if svc.running else 'FAILED')}")
            for err in svc.errors[:3]:
                console.print(f"      [red]{err[:150]}[/red]")

        # Sauvegarder les infos d'infra pour que tout le monde puisse les lire
        self._save_infra_json()

        return self._services

    def _save_infra_json(self) -> None:
        """Écrit .oryn/infra.json avec les ports/URLs détectés."""
        infra_path = self.workdir / ".oryn" / "infra.json"
        data = {
            "web_url": self.web_base_url,
            "web_port": self._web_port,
            "web_healthy": self._services.get("web", ServiceStatus(name="web")).healthy,
            "convex_running": self._services.get("convex", ServiceStatus(name="convex")).running,
            "android_running": self._services.get("android", ServiceStatus(name="android")).running,
            "expo_running": self._services.get("expo", ServiceStatus(name="expo")).running,
        }
        infra_path.write_text(json.dumps(data, indent=2))
        console.print(f"  [dim]infra.json → web={self.web_base_url}[/dim]")

    def stop_all(self) -> None:
        """Kill tous les processus lancés."""
        console.print("[dim]Cleaning up infrastructure...[/dim]")

        for proc in self._procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        # Safety kill
        for pattern in ["pnpm dev", "expo start", "convex dev", "metro"]:
            self._pkill(pattern)

        self._procs.clear()
        self._services.clear()

    # -------------------------------------------------------------------------
    # Convex
    # -------------------------------------------------------------------------

    def _has_convex(self) -> bool:
        """Vérifie si le projet utilise Convex."""
        return (
            (self.workdir / "convex").exists()
            or (self.workdir / "packages" / "api" / "convex").exists()
        )

    def _start_convex(self) -> ServiceStatus:
        """Lance `npx convex dev` et attend qu'il soit prêt."""
        status = ServiceStatus(name="convex")
        console.print("[blue]Starting Convex...[/blue]")

        # Trouver le dossier convex
        convex_dir = self.workdir
        if (self.workdir / "packages" / "api").exists():
            convex_dir = self.workdir / "packages" / "api"

        try:
            proc = subprocess.Popen(
                ["npx", "convex", "dev"],
                cwd=str(convex_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._procs.append(proc)
            status.pid = proc.pid
        except FileNotFoundError:
            status.errors.append("npx not found — install Node.js")
            return status

        # Attendre que Convex soit prêt (max 45s)
        status = self._wait_for_output(
            proc, status,
            ready_keywords=["ready", "convex", "synced", "listening", "✔", "dashboard"],
            error_keywords=["error", "failed", "fatal"],
            timeout=45,
        )

        if status.running:
            console.print("[green]  Convex dev server ready[/green]")
        else:
            # Fallback : essayer convex deploy pour un env de dev
            console.print("[yellow]  Convex dev pas prêt, try sans backend[/yellow]")

        return status

    # -------------------------------------------------------------------------
    # Web server
    # -------------------------------------------------------------------------

    def _start_web(self) -> ServiceStatus:
        """Lance le serveur web, détecte le port, et vérifie qu'il répond."""
        status = ServiceStatus(name="web")
        console.print("[blue]Starting web server...[/blue]")

        web_dir = self.workdir / "apps" / "web"

        # Install deps si nécessaire
        if not (web_dir / "node_modules").exists():
            console.print("  [dim]Installing deps...[/dim]")
            subprocess.run(["pnpm", "install"], cwd=str(self.workdir), capture_output=True, timeout=120)

        try:
            proc = subprocess.Popen(
                ["pnpm", "dev"],
                cwd=str(web_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._procs.append(proc)
            status.pid = proc.pid
        except FileNotFoundError:
            status.errors.append("pnpm not found")
            return status

        # Attendre le serveur (max 30s) + détecter le port
        status = self._wait_for_output(
            proc, status,
            ready_keywords=["ready", "listening", "localhost:", "started", "http://", "port"],
            error_keywords=["error", "failed", "eaddrinuse"],
            timeout=30,
        )

        # Détecter le port depuis les logs
        detected_port = self._detect_port_from_logs(status.logs)
        if detected_port:
            self._web_port = detected_port
            console.print(f"  [green]Port détecté : {detected_port}[/green]")
        else:
            self._web_port = DEFAULT_WEB_PORT
            console.print(f"  [dim]Port par défaut : {DEFAULT_WEB_PORT}[/dim]")

        status.port = self._web_port
        status.url = self.web_base_url

        # Health check HTTP avec retries sur le bon port
        if status.running:
            status.healthy = self._http_health_check(self.web_base_url, retries=5, delay=2)
            if status.healthy:
                console.print("[green]  Web server healthy (HTTP 200)[/green]")
            else:
                status.errors.append("Web server running but HTTP check failed")
                console.print("[yellow]  Web server running but HTTP check failed[/yellow]")

        return status

    # -------------------------------------------------------------------------
    # Android emulator
    # -------------------------------------------------------------------------

    def _start_android_emulator(self) -> ServiceStatus:
        """Lance l'émulateur Android et attend le boot complet."""
        status = ServiceStatus(name="android")
        console.print("[blue]Starting Android emulator...[/blue]")

        # Check adb
        if not self._cmd_exists("adb"):
            status.errors.append("adb not found — install Android SDK")
            return status

        # Check si déjà running
        if self._android_emulator_running():
            status.running = True
            status.healthy = True
            console.print("[green]  Emulateur déjà running[/green]")
            return status

        # Trouver un AVD
        if not self._cmd_exists("emulator"):
            status.errors.append("emulator not found — install Android SDK emulator")
            return status

        try:
            avd_out = subprocess.run(
                ["emulator", "-list-avds"],
                capture_output=True, text=True, timeout=10,
            )
            avds = [a.strip() for a in avd_out.stdout.strip().split("\n") if a.strip()]
        except Exception:
            avds = []

        if not avds:
            status.errors.append("Aucun AVD trouvé — créer via Android Studio > Device Manager")
            return status

        avd = avds[0]
        console.print(f"  [dim]Launching AVD: {avd}[/dim]")

        # Lancer l'émulateur
        try:
            proc = subprocess.Popen(
                ["emulator", f"@{avd}", "-no-audio", "-no-boot-anim", "-gpu", "auto"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._procs.append(proc)
            status.pid = proc.pid
        except Exception as e:
            status.errors.append(f"Failed to start emulator: {e}")
            return status

        # Attendre le boot complet (max 90s)
        console.print("  [dim]Waiting for emulator boot...[/dim]")
        status.running = self._wait_for_android_boot(timeout=90)

        if status.running:
            # Vérifier que l'écran est déverrouillé
            self._unlock_android()
            status.healthy = True
            console.print("[green]  Emulateur Android booted + unlocked[/green]")
        else:
            status.errors.append("Emulator boot timeout (90s)")
            console.print("[red]  Emulateur boot timeout[/red]")

        return status

    def _android_emulator_running(self) -> bool:
        """Vérifie si un émulateur Android est déjà running."""
        try:
            out = subprocess.run(
                ["adb", "devices"],
                capture_output=True, text=True, timeout=5,
            )
            return "emulator" in out.stdout
        except Exception:
            return False

    def _wait_for_android_boot(self, timeout: int = 90) -> bool:
        """Attend que l'émulateur Android ait fini de booter."""
        # Phase 1 : attendre que adb voit le device
        try:
            subprocess.run(["adb", "wait-for-device"], timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

        # Phase 2 : attendre que le boot soit complet (sys.boot_completed=1)
        start = time.time()
        while time.time() - start < timeout:
            try:
                out = subprocess.run(
                    ["adb", "shell", "getprop", "sys.boot_completed"],
                    capture_output=True, text=True, timeout=5,
                )
                if out.stdout.strip() == "1":
                    return True
            except Exception:
                pass
            time.sleep(3)

        return False

    def _unlock_android(self) -> None:
        """Déverrouille l'écran de l'émulateur Android."""
        try:
            # Wake up
            subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_WAKEUP"], timeout=5)
            time.sleep(1)
            # Swipe up to unlock
            subprocess.run(["adb", "shell", "input", "swipe", "500", "1500", "500", "500"], timeout=5)
            time.sleep(1)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Expo mobile
    # -------------------------------------------------------------------------

    def _start_expo(self) -> ServiceStatus:
        """Lance Expo et attend que Metro soit prêt."""
        status = ServiceStatus(name="expo")
        console.print("[blue]Starting Expo...[/blue]")

        mobile_dir = self.workdir / "apps" / "mobile"

        # Install deps si nécessaire
        if not (mobile_dir / "node_modules").exists():
            console.print("  [dim]Installing mobile deps...[/dim]")
            subprocess.run(["pnpm", "install"], cwd=str(mobile_dir), capture_output=True, timeout=120)

        try:
            proc = subprocess.Popen(
                ["npx", "expo", "start", "--android"],
                cwd=str(mobile_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._procs.append(proc)
            status.pid = proc.pid
        except FileNotFoundError:
            status.errors.append("npx not found")
            return status

        # Attendre Metro bundler (max 60s)
        status = self._wait_for_output(
            proc, status,
            ready_keywords=["bundled", "running on", "open on", "metro", "started", "android"],
            error_keywords=["error", "failed", "cannot find", "red screen"],
            timeout=60,
        )

        if status.running:
            # Attendre un peu que l'app s'installe sur l'émulateur
            time.sleep(5)

            # Check logcat pour erreurs JS
            logcat_errors = self._get_logcat_errors()
            if logcat_errors:
                status.errors.extend(logcat_errors[:5])
                console.print(f"  [yellow]{len(logcat_errors)} erreurs JS dans logcat[/yellow]")
            else:
                status.healthy = True
                console.print("[green]  Expo app running on emulator[/green]")

        return status

    def _get_logcat_errors(self) -> list[str]:
        """Récupère les erreurs JS depuis logcat Android."""
        try:
            out = subprocess.run(
                ["adb", "logcat", "-d", "-s", "ReactNativeJS:E", "ReactNative:E"],
                capture_output=True, text=True, timeout=10,
            )
            errors = []
            for line in out.stdout.split("\n"):
                line = line.strip()
                if line and ("error" in line.lower() or "fatal" in line.lower()):
                    errors.append(line[:200])
            return errors
        except Exception:
            return []

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _wait_for_output(
        self,
        proc: subprocess.Popen,
        status: ServiceStatus,
        ready_keywords: list[str],
        error_keywords: list[str],
        timeout: int = 30,
    ) -> ServiceStatus:
        """Attend des mots-clés dans stdout d'un process."""
        logs_lines: list[str] = []
        start = time.time()

        while time.time() - start < timeout:
            if proc.poll() is not None:
                remaining = proc.stdout.read() if proc.stdout else ""
                if remaining:
                    logs_lines.append(remaining)
                status.errors.append(f"Process exited prematurely (code {proc.returncode})")
                break

            try:
                line = proc.stdout.readline() if proc.stdout else ""
            except Exception:
                line = ""

            if not line:
                time.sleep(0.5)
                continue

            line = line.rstrip()
            logs_lines.append(line)
            console.print(f"  [dim]{line[:150]}[/dim]")

            lower = line.lower()
            if any(kw in lower for kw in ready_keywords):
                status.running = True
                status.healthy = True
                break

            if any(kw in lower for kw in error_keywords):
                status.errors.append(line[:200])

        status.logs = "\n".join(logs_lines[-80:])

        if not status.running and not status.errors:
            status.errors.append(f"Timeout ({timeout}s) — no ready signal detected")

        return status

    def _http_health_check(self, url: str, retries: int = 5, delay: int = 2) -> bool:
        """HTTP GET avec retries."""
        for i in range(retries):
            try:
                out = subprocess.run(
                    ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url],
                    capture_output=True, text=True, timeout=10,
                )
                code = out.stdout.strip()
                if code.startswith("2") or code.startswith("3"):
                    return True
                console.print(f"  [dim]HTTP {code} (attempt {i+1}/{retries})[/dim]")
            except Exception:
                pass
            time.sleep(delay)
        return False

    def _detect_port_from_logs(self, logs: str) -> int | None:
        """Détecte le port du serveur web depuis les logs de démarrage.

        Cherche des patterns comme :
        - "localhost:3001"
        - "http://127.0.0.1:5173"
        - "port 4000"
        - "listening on :8080"
        - "ready at http://localhost:3000"
        """
        if not logs:
            return None

        # Pattern 1 : URL complète avec port
        url_match = re.search(r'https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d+)', logs)
        if url_match:
            return int(url_match.group(1))

        # Pattern 2 : "port XXXX" ou "Port: XXXX"
        port_match = re.search(r'port[:\s]+(\d{4,5})', logs, re.IGNORECASE)
        if port_match:
            return int(port_match.group(1))

        # Pattern 3 : ":XXXX" (listening on :3000)
        colon_match = re.search(r'(?:listening|started|ready)\s+(?:on\s+)?:(\d{4,5})', logs, re.IGNORECASE)
        if colon_match:
            return int(colon_match.group(1))

        return None

    def _cmd_exists(self, cmd: str) -> bool:
        """Vérifie si une commande existe."""
        try:
            subprocess.run(["which", cmd], capture_output=True, timeout=5, check=True)
            return True
        except Exception:
            return False

    def _pkill(self, pattern: str) -> None:
        """Kill les processus matchant un pattern."""
        try:
            subprocess.run(["pkill", "-f", pattern], capture_output=True, timeout=5)
        except Exception:
            pass
