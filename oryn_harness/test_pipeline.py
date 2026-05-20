"""Scripts de test helpers injectés dans le contexte des agents.

Comme pour playwright_runner.py, on ne fait PAS tourner les tests
nous-mêmes — les agents (Generator/Evaluator) ont Bash et peuvent
lancer ces scripts. Ce module pose les helpers dans .oryn/scripts/.
"""
from __future__ import annotations

from pathlib import Path


MAESTRO_HELPER_SCRIPT = '''#!/bin/bash
# Helper pour lancer les tests Maestro sur mobile
# Usage : .oryn/scripts/maestro_helper.sh <platform> [flow_dir]
# Exemples :
#   .oryn/scripts/maestro_helper.sh android
#   .oryn/scripts/maestro_helper.sh ios apps/mobile/maestro/flows/auth/
#   .oryn/scripts/maestro_helper.sh android apps/mobile/maestro/flows/

set -euo pipefail

PLATFORM="${1:-android}"
FLOW_DIR="${2:-apps/mobile/maestro/flows/}"

echo "=== Maestro E2E Tests ==="
echo "Platform: $PLATFORM"
echo "Flows: $FLOW_DIR"

# Vérifier que Maestro est installé
if ! command -v maestro &> /dev/null; then
    echo "ERROR: Maestro not installed. Install with: curl -Ls https://get.maestro.mobile.dev | bash"
    exit 1
fi

# Démarrer l'émulateur/simulateur si pas déjà running
if [ "$PLATFORM" = "android" ]; then
    # Check si un emulator tourne déjà
    if ! adb devices | grep -q "emulator"; then
        echo "Starting Android emulator..."
        # Lister les AVDs disponibles
        AVDS=$(emulator -list-avds 2>/dev/null | head -1)
        if [ -z "$AVDS" ]; then
            echo "ERROR: No Android AVD found. Create one via Android Studio > Device Manager"
            exit 1
        fi
        emulator @"$AVDS" -no-window -no-audio &
        sleep 15  # Attendre le boot
        adb wait-for-device
        echo "Emulator started: $AVDS"
    else
        echo "Android emulator already running"
    fi
elif [ "$PLATFORM" = "ios" ]; then
    # Check si un simulateur tourne
    if ! xcrun simctl list devices booted | grep -q "Booted"; then
        echo "Starting iOS simulator..."
        # Trouver le dernier iPhone disponible
        DEVICE=$(xcrun simctl list devices available | grep "iPhone" | tail -1 | sed 's/.*(//' | sed 's/).*//')
        if [ -z "$DEVICE" ]; then
            echo "ERROR: No iOS simulator found"
            exit 1
        fi
        xcrun simctl boot "$DEVICE"
        sleep 10
        echo "Simulator started: $DEVICE"
    else
        echo "iOS simulator already running"
    fi
fi

# Build l'app mobile si nécessaire
if [ -d "apps/mobile" ]; then
    echo "Building mobile app..."
    if [ "$PLATFORM" = "android" ]; then
        cd apps/mobile && npx expo run:android --no-install 2>/dev/null || true && cd ../..
    elif [ "$PLATFORM" = "ios" ]; then
        cd apps/mobile && npx expo run:ios --no-install 2>/dev/null || true && cd ../..
    fi
fi

# Lancer les tests Maestro
echo "Running Maestro tests..."
maestro test "$FLOW_DIR" --format junit --output .oryn/test_results/maestro_results.xml 2>&1

echo "=== Maestro tests complete ==="
'''


LIGHTHOUSE_HELPER_SCRIPT = '''#!/bin/bash
# Helper pour lancer Lighthouse CI
# Usage : .oryn/scripts/lighthouse_helper.sh <url> [output_dir]
# Exemples :
#   .oryn/scripts/lighthouse_helper.sh http://localhost:3000
#   .oryn/scripts/lighthouse_helper.sh http://localhost:3000 .oryn/test_results/

set -euo pipefail

URL="${1:-http://localhost:3000}"
OUTPUT_DIR="${2:-.oryn/test_results}"

mkdir -p "$OUTPUT_DIR"

echo "=== Lighthouse Audit ==="
echo "URL: $URL"

# Méthode 1 : lhci (si installé)
if command -v lhci &> /dev/null; then
    lhci autorun --collect.url="$URL" --upload.target=filesystem --upload.outputDir="$OUTPUT_DIR/lighthouse" 2>&1
# Méthode 2 : lighthouse directement
elif command -v lighthouse &> /dev/null; then
    lighthouse "$URL" \
        --output=json,html \
        --output-path="$OUTPUT_DIR/lighthouse-report" \
        --chrome-flags="--headless --no-sandbox" \
        --quiet 2>&1

    # Parser les scores
    if [ -f "$OUTPUT_DIR/lighthouse-report.report.json" ]; then
        python3 -c "
import json
with open('$OUTPUT_DIR/lighthouse-report.report.json') as f:
    r = json.load(f)
cats = r.get('categories', {})
for name, cat in cats.items():
    score = int(cat.get('score', 0) * 100)
    print(f'{name}: {score}')
"
    fi
else
    echo "WARNING: Neither lhci nor lighthouse CLI found"
    echo "Install with: npm install -g @lhci/cli lighthouse"
    # Fallback : utiliser Playwright pour un check basique
    echo "Running basic performance check with curl..."
    START=$(date +%s%N)
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$URL" 2>/dev/null || echo "000")
    END=$(date +%s%N)
    DURATION=$(( (END - START) / 1000000 ))
    echo "HTTP $HTTP_CODE — ${DURATION}ms"
    if [ "$DURATION" -gt 3000 ]; then
        echo "WARNING: Page load > 3s"
    fi
fi

echo "=== Lighthouse audit complete ==="
'''


K6_LOAD_TEST_TEMPLATE = '''// k6 load test template
// Usage : k6 run tests/load/api-load.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('errors');
const responseTime = new Trend('response_time');

export const options = {
  stages: [
    { duration: '10s', target: 10 },  // Ramp up
    { duration: '30s', target: 50 },  // Load
    { duration: '10s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],  // 95% des requêtes < 500ms
    errors: ['rate<0.01'],              // Taux d'erreur < 1%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:3000';

export default function () {
  // Adapter ces endpoints au projet
  const endpoints = [
    '/',
    '/api/health',
  ];

  for (const endpoint of endpoints) {
    const res = http.get(`${BASE_URL}${endpoint}`);
    check(res, {
      'status is 200': (r) => r.status === 200,
      'response time < 500ms': (r) => r.timings.duration < 500,
    });
    errorRate.add(res.status !== 200);
    responseTime.add(res.timings.duration);
  }

  sleep(1);
}
'''


SECURITY_SCAN_SCRIPT = '''#!/bin/bash
# Helper pour les scans de sécurité
# Usage : .oryn/scripts/security_scan.sh [url]
# Exemples :
#   .oryn/scripts/security_scan.sh
#   .oryn/scripts/security_scan.sh http://localhost:3000

set -euo pipefail

URL="${1:-}"
OUTPUT_DIR=".oryn/test_results"
mkdir -p "$OUTPUT_DIR"

echo "=== Security Scan ==="

# 1. npm audit
echo "--- npm audit ---"
if command -v pnpm &> /dev/null; then
    pnpm audit --json > "$OUTPUT_DIR/npm-audit.json" 2>/dev/null || true
    pnpm audit 2>/dev/null || true
elif command -v npm &> /dev/null; then
    npm audit --json > "$OUTPUT_DIR/npm-audit.json" 2>/dev/null || true
    npm audit 2>/dev/null || true
fi

# 2. Gitleaks (secrets dans le code)
echo "--- Gitleaks ---"
if command -v gitleaks &> /dev/null; then
    gitleaks detect --source . --report-path "$OUTPUT_DIR/gitleaks.json" --report-format json 2>&1 || true
else
    echo "WARNING: gitleaks not installed (brew install gitleaks)"
    # Fallback basique
    echo "Checking for common secret patterns..."
    grep -rn "AKIA[0-9A-Z]\\{16\\}" --include="*.ts" --include="*.tsx" --include="*.js" --include="*.env" . 2>/dev/null && echo "FOUND: Possible AWS key" || echo "OK: No AWS keys found"
    grep -rn "sk-[a-zA-Z0-9]\\{20,\\}" --include="*.ts" --include="*.tsx" --include="*.js" --include="*.env" . 2>/dev/null && echo "FOUND: Possible API key" || echo "OK: No API keys found"
    grep -rn "password.*=.*['\"][^'\"]*['\"]" --include="*.ts" --include="*.tsx" --include="*.js" . 2>/dev/null | grep -v "test" | grep -v ".oryn" && echo "FOUND: Possible hardcoded password" || echo "OK: No hardcoded passwords"
fi

# 3. OWASP ZAP baseline (si URL fournie et Docker disponible)
if [ -n "$URL" ] && command -v docker &> /dev/null; then
    echo "--- OWASP ZAP Baseline ---"
    docker run --rm -t --network host \
        ghcr.io/zaproxy/zaproxy:stable \
        zap-baseline.py -t "$URL" -J "$OUTPUT_DIR/zap-report.json" 2>&1 || true
else
    if [ -z "$URL" ]; then
        echo "SKIP: ZAP scan (no URL provided)"
    else
        echo "SKIP: ZAP scan (Docker not available)"
    fi
fi

echo "=== Security scan complete ==="
'''


MAESTRO_FLOW_TEMPLATE = '''# Template Maestro flow
# Copier et adapter pour chaque feature
appId: {app_id}
---
# Login flow (réutilisable)
- launchApp
- assertVisible: "Welcome"  # ou l'écran de login

# Exemple : remplir un formulaire
# - tapOn: "Email"
# - inputText: "test@example.com"
# - tapOn: "Password"
# - inputText: "password123"
# - tapOn: "Sign In"
# - assertVisible: "Dashboard"

# Screenshot pour vérification
- takeScreenshot: "flow-result"
'''


def install_test_helpers(workdir: Path) -> None:
    """Installe les scripts de test dans .oryn/scripts/."""
    scripts_dir = workdir / ".oryn" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    helpers = {
        "maestro_helper.sh": MAESTRO_HELPER_SCRIPT,
        "lighthouse_helper.sh": LIGHTHOUSE_HELPER_SCRIPT,
        "security_scan.sh": SECURITY_SCAN_SCRIPT,
    }

    for name, content in helpers.items():
        path = scripts_dir / name
        path.write_text(content)
        path.chmod(0o755)

    # Templates (pas exécutables)
    templates_dir = workdir / ".oryn" / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)

    (templates_dir / "k6-load-test.js").write_text(K6_LOAD_TEST_TEMPLATE)
    (templates_dir / "maestro-flow.yaml").write_text(MAESTRO_FLOW_TEMPLATE)

    # Test results dir
    (workdir / ".oryn" / "test_results").mkdir(parents=True, exist_ok=True)
