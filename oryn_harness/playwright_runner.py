"""Playwright runner injecté dans le contexte de l'Evaluator.

On ne fait PAS tourner Playwright nous-mêmes — l'Evaluator (qui est
un agent Claude Code) a Bash et peut lancer Playwright via les scripts
qu'on lui fournit. Ce module pose juste les helpers que l'Evaluator
peut appeler.
"""
from __future__ import annotations

from pathlib import Path

# Script Python que l'Evaluator peut exécuter via Bash pour tester un endpoint web.
# On le pose dans .oryn/scripts/ au début du run pour que l'Evaluator y ait accès.

PLAYWRIGHT_HELPER_SCRIPT = '''#!/usr/bin/env python3
"""Helper Playwright pour l'Evaluator.

Usage :
    python .oryn/scripts/pw_check.py <url> [--screenshot path.png] [--click selector] [--wait ms]

Examples :
    # Screenshot la home
    python .oryn/scripts/pw_check.py http://localhost:3000 --screenshot /tmp/home.png

    # Clique un bouton et capture le résultat
    python .oryn/scripts/pw_check.py http://localhost:3000 --click "button.save" --screenshot /tmp/after.png

    # Récupère le DOM rendu
    python .oryn/scripts/pw_check.py http://localhost:3000 --dump-html
"""
import argparse
import sys
from playwright.sync_api import sync_playwright


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--screenshot", help="Path pour sauvegarder un screenshot")
    p.add_argument("--click", help="Sélecteur CSS à cliquer avant screenshot")
    p.add_argument("--fill", nargs=2, metavar=("SELECTOR", "VALUE"), help="Remplir un input")
    p.add_argument("--wait", type=int, default=1000, help="Wait ms après chargement")
    p.add_argument("--dump-html", action="store_true", help="Print le HTML rendu")
    p.add_argument("--dump-console", action="store_true", help="Print les logs console")
    p.add_argument("--headed", action="store_true", help="Mode visible (debug)")
    args = p.parse_args()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context()
        page = context.new_page()

        console_logs = []
        if args.dump_console:
            page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))

        try:
            page.goto(args.url, wait_until="networkidle", timeout=15000)
        except Exception as e:
            print(f"NAVIGATION_ERROR: {e}", file=sys.stderr)
            sys.exit(2)

        page.wait_for_timeout(args.wait)

        if args.fill:
            page.fill(args.fill[0], args.fill[1])

        if args.click:
            try:
                page.click(args.click, timeout=5000)
                page.wait_for_timeout(args.wait)
            except Exception as e:
                print(f"CLICK_ERROR: {e}", file=sys.stderr)

        if args.screenshot:
            page.screenshot(path=args.screenshot, full_page=True)
            print(f"SCREENSHOT_SAVED: {args.screenshot}")

        if args.dump_html:
            print("--- HTML ---")
            print(page.content())

        if args.dump_console:
            print("--- CONSOLE ---")
            for line in console_logs:
                print(line)

        browser.close()


if __name__ == "__main__":
    main()
'''


PLAYWRIGHT_SCREENSHOT_SCRIPT = '''#!/usr/bin/env python3
"""Helper Playwright pour le Researcher : screenshot de sites externes.

Usage :
    python .oryn/scripts/pw_screenshot.py <url> --output path.png [--viewport 1440x900] [--wait ms]

Examples :
    python .oryn/scripts/pw_screenshot.py https://linear.app --output .oryn/references/01_linear.png
    python .oryn/scripts/pw_screenshot.py https://notion.so --output .oryn/references/02_notion.png --viewport 1440x900
"""
import argparse
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--output", required=True, help="Path pour sauvegarder le screenshot")
    p.add_argument("--viewport", default="1440x900", help="Viewport WxH")
    p.add_argument("--wait", type=int, default=3000, help="Wait ms après chargement")
    p.add_argument("--full-page", action="store_true", default=True, help="Screenshot pleine page")
    p.add_argument("--no-full-page", action="store_true", help="Screenshot viewport seulement")
    args = p.parse_args()

    w, h = (int(x) for x in args.viewport.split("x"))
    full_page = not args.no_full_page

    # Créer le dossier parent si nécessaire
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": w, "height": h},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            page.goto(args.url, wait_until="networkidle", timeout=20000)
        except Exception as e:
            print(f"NAVIGATION_ERROR: {e}", file=sys.stderr)
            # Essayer quand même de screenshot ce qui est chargé
            try:
                page.screenshot(path=args.output, full_page=full_page)
                print(f"SCREENSHOT_SAVED (partial): {args.output}")
            except Exception:
                pass
            browser.close()
            sys.exit(2)

        page.wait_for_timeout(args.wait)

        # Fermer les popups/modals courants (cookies, newsletters)
        for selector in [
            "button:has-text('Accept')",
            "button:has-text('Got it')",
            "button:has-text('Close')",
            "[aria-label='Close']",
            ".cookie-banner button",
        ]:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass

        page.screenshot(path=args.output, full_page=full_page)
        print(f"SCREENSHOT_SAVED: {args.output}")

        browser.close()


if __name__ == "__main__":
    main()
'''


def install_helpers(workdir: Path) -> None:
    """Installe les scripts Playwright dans .oryn/scripts/."""
    scripts_dir = workdir / ".oryn" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    script_path = scripts_dir / "pw_check.py"
    script_path.write_text(PLAYWRIGHT_HELPER_SCRIPT)
    script_path.chmod(0o755)

    screenshot_path = scripts_dir / "pw_screenshot.py"
    screenshot_path.write_text(PLAYWRIGHT_SCREENSHOT_SCRIPT)
    screenshot_path.chmod(0o755)
