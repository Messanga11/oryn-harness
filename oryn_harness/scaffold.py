"""Scaffold déterministe — scripts Python qui créent la structure du monorepo.

Pas d'IA ici. Des scripts DÉTERMINISTES qui génèrent les fichiers de config,
la structure des dossiers, et les dépendances. L'IA ne doit PAS deviner
comment configurer Turborepo ou pnpm — on le fait nous-mêmes.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from rich.console import Console

console = Console()


def scaffold_monorepo(workdir: Path, web_port: int = 3000) -> None:
    """Crée la structure complète du monorepo."""
    console.print("[bold blue]Scaffolding monorepo...[/bold blue]")

    # Écrire le port fixe dans .oryn/infra.json dès le scaffold
    oryn_dir = workdir / ".oryn"
    oryn_dir.mkdir(parents=True, exist_ok=True)
    _write(oryn_dir / "infra.json", json.dumps({
        "web_url": f"http://localhost:{web_port}",
        "web_port": web_port,
    }, indent=2))

    workdir.mkdir(parents=True, exist_ok=True)

    # Root files
    _write(workdir / "package.json", json.dumps({
        "name": "oryn-monorepo",
        "private": True,
        "scripts": {
            "dev": "turbo dev",
            "build": "turbo build",
            "test": "turbo test",
            "test:e2e": "turbo test:e2e",
            "lint": "turbo lint",
            "lint:fix": "biome check --write . && eslint --fix .",
            "quality": "turbo lint && knip && biome check .",
            "clean": "turbo clean",
        },
        "devDependencies": {
            "turbo": "^2",
            "@biomejs/biome": "^1",
            "eslint": "^9",
            "eslint-plugin-react": "^7",
            "eslint-plugin-jsx-a11y": "^6",
            "eslint-plugin-tailwindcss": "^3",
            "knip": "^5",
        },
    }, indent=2))

    # Biome config — fast linter/formatter
    _write(workdir / "biome.json", json.dumps({
        "$schema": "https://biomejs.dev/schemas/1.9.4/schema.json",
        "organizeImports": {"enabled": True},
        "formatter": {"indentStyle": "space", "indentWidth": 2, "lineWidth": 100},
        "linter": {
            "enabled": True,
            "rules": {
                "recommended": True,
                "complexity": {
                    "noExcessiveCognitiveComplexity": {"level": "warn", "options": {"maxAllowedComplexity": 15}},
                },
                "suspicious": {
                    "noConsole": "warn",
                    "noDebugger": "error",
                },
                "style": {
                    "useConst": "error",
                    "noVar": "error",
                },
            },
        },
    }, indent=2))

    # ESLint config — design system enforcement + a11y
    _write(workdir / "eslint.config.js", """import react from 'eslint-plugin-react'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import tailwind from 'eslint-plugin-tailwindcss'

export default [
  react.configs.flat.recommended,
  ...tailwind.configs['flat/recommended'],
  jsxA11y.flatConfigs.recommended,
  {
    rules: {
      // BAN raw HTML elements — force design system usage
      'react/forbid-elements': ['error', {
        forbid: [
          { element: 'div', message: 'Use <Box> from @repo/ui' },
          { element: 'span', message: 'Use <Box> or <Typography> from @repo/ui' },
          { element: 'p', message: 'Use <Typography variant="body"> from @repo/ui' },
          { element: 'h1', message: 'Use <Typography variant="h1"> from @repo/ui' },
          { element: 'h2', message: 'Use <Typography variant="h2"> from @repo/ui' },
          { element: 'h3', message: 'Use <Typography variant="h3"> from @repo/ui' },
          { element: 'h4', message: 'Use <Typography variant="h4"> from @repo/ui' },
          { element: 'h5', message: 'Use <Typography variant="h5"> from @repo/ui' },
          { element: 'h6', message: 'Use <Typography variant="h6"> from @repo/ui' },
          { element: 'button', message: 'Use <Button> from @repo/ui' },
          { element: 'input', message: 'Use <Input> from @repo/ui' },
          { element: 'textarea', message: 'Use <TextArea> from @repo/ui' },
          { element: 'img', message: 'Use <Image> from @repo/ui' },
          { element: 'a', message: 'Use <Link> from @repo/ui or router Link' },
          { element: 'ul', message: 'Use <List> from @repo/ui' },
          { element: 'li', message: 'Use <ListItem> from @repo/ui' },
        ],
      }],
      // Accessibility
      'jsx-a11y/alt-text': 'error',
      'jsx-a11y/aria-props': 'error',
      'jsx-a11y/click-events-have-key-events': 'warn',
      // Tailwind
      'tailwindcss/no-custom-classname': 'warn',
      'tailwindcss/classnames-order': 'warn',
    },
    settings: {
      react: { version: 'detect' },
    },
  },
  {
    // Allow raw elements in packages/ui (the design system itself)
    files: ['packages/ui/**'],
    rules: { 'react/forbid-elements': 'off' },
  },
]
""")

    # Knip config — dead code detection
    _write(workdir / "knip.json", json.dumps({
        "workspaces": {
            "packages/ui": {"entry": ["src/index.ts"]},
            "packages/features": {"entry": ["src/index.ts"]},
            "apps/web": {"entry": ["src/routes/**/*.tsx"]},
            "apps/mobile": {"entry": ["app/**/*.tsx"]},
        },
    }, indent=2))

    _write(workdir / "pnpm-workspace.yaml", "packages:\n  - 'apps/*'\n  - 'packages/*'\n")

    _write(workdir / "turbo.json", json.dumps({
        "$schema": "https://turbo.build/schema.json",
        "tasks": {
            "build": {"dependsOn": ["^build"], "outputs": ["dist/**", ".output/**"]},
            "dev": {"persistent": True, "cache": False},
            "test": {"dependsOn": ["^build"]},
            "test:e2e": {"dependsOn": ["build"]},
            "lint": {},
            "clean": {"cache": False},
        },
    }, indent=2))

    _write(workdir / ".gitignore", "\n".join([
        "node_modules/", "dist/", ".output/", ".turbo/", ".expo/",
        "*.tsbuildinfo", ".env", ".env.local", "coverage/",
        ".oryn/traces/", ".oryn/evidence/", ".oryn/test_results/",
    ]))

    _write(workdir / ".npmrc", "auto-install-peers=true\nshamefully-hoist=true\n")

    # Packages
    _scaffold_ui_package(workdir / "packages" / "ui")
    _scaffold_features_package(workdir / "packages" / "features")
    _scaffold_api_package(workdir / "packages" / "api")
    _scaffold_config_package(workdir / "packages" / "config")

    console.print("[green]✓ Monorepo scaffolded[/green]")


def add_web_app(workdir: Path, name: str = "web", port: int = 3000) -> None:
    """Ajoute une app TanStack Start au monorepo avec un port fixe."""
    console.print(f"[blue]Adding web app: {name} (port {port})[/blue]")

    app_dir = workdir / "apps" / name
    app_dir.mkdir(parents=True, exist_ok=True)

    _write(app_dir / "package.json", json.dumps({
        "name": f"@repo/{name}",
        "private": True,
        "type": "module",
        "scripts": {
            "dev": f"vinxi dev --port {port}",
            "build": "vinxi build",
            "start": f"vinxi start --port {port}",
            "test": "vitest run",
            "test:e2e": "playwright test",
        },
        "dependencies": {
            "@tanstack/react-router": "^1",
            "@tanstack/react-start": "^1",
            "react": "^19",
            "react-dom": "^19",
            "vinxi": "^0.5",
            "@repo/ui": "workspace:*",
            "@repo/features": "workspace:*",
            "@repo/api": "workspace:*",
        },
        "devDependencies": {
            "@types/react": "^19",
            "typescript": "^5",
            "vitest": "^3",
            "@playwright/test": "^1",
            "tailwindcss": "^4",
            "@repo/config": "workspace:*",
        },
    }, indent=2))

    _write(app_dir / "tsconfig.json", json.dumps({
        "extends": "@repo/config/tsconfig.web.json",
        "compilerOptions": {"outDir": "dist", "rootDir": "src"},
        "include": ["src"],
    }, indent=2))

    # App config
    src = app_dir / "src"
    src.mkdir(exist_ok=True)
    routes = src / "routes"
    routes.mkdir(exist_ok=True)

    _write(app_dir / "app.config.ts", """import { defineConfig } from '@tanstack/react-start/config'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  vite: { plugins: [tailwindcss()] },
})
""")

    _write(src / "router.tsx", """import { createRouter as createTanStackRouter } from '@tanstack/react-router'
import { routeTree } from './routeTree.gen'

export function createRouter() {
  return createTanStackRouter({ routeTree })
}

declare module '@tanstack/react-router' {
  interface Register { router: ReturnType<typeof createRouter> }
}
""")

    _write(routes / "__root.tsx", """import { Outlet, createRootRoute } from '@tanstack/react-router'

export const Route = createRootRoute({
  component: () => <Outlet />,
})
""")

    _write(routes / "index.tsx", """import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/')({
  component: () => <div>Home — replace with @repo/features page</div>,
})
""")

    # Playwright config
    _write(app_dir / "playwright.config.ts", f"""import {{ defineConfig }} from '@playwright/test'

export default defineConfig({{
  testDir: './tests',
  webServer: {{ command: 'pnpm dev', port: {port}, reuseExistingServer: true }},
  use: {{ baseURL: 'http://localhost:{port}' }},
}})
""")

    (app_dir / "tests").mkdir(exist_ok=True)

    console.print(f"[green]✓ Web app '{name}' added[/green]")


def add_mobile_app(workdir: Path, name: str = "mobile") -> None:
    """Ajoute une app Expo au monorepo."""
    console.print(f"[blue]Adding mobile app: {name}[/blue]")

    app_dir = workdir / "apps" / name
    app_dir.mkdir(parents=True, exist_ok=True)

    _write(app_dir / "package.json", json.dumps({
        "name": f"@repo/{name}",
        "private": True,
        "main": "expo-router/entry",
        "scripts": {
            "dev": "expo start",
            "android": "expo start --android",
            "ios": "expo start --ios",
            "test": "vitest run",
        },
        "dependencies": {
            "expo": "~52",
            "expo-router": "~4",
            "expo-status-bar": "~2",
            "expo-updates": "~0.27",
            "expo-image": "~2",
            "react": "^19",
            "react-native": "~0.79",
            "react-native-reanimated": "~3",
            "react-native-safe-area-context": "~5",
            "react-native-screens": "~4",
            "react-native-web": "~0.19",
            "nativewind": "^4",
            "@gluestack-ui/nativewind-utils": "^1",
            "@repo/ui": "workspace:*",
            "@repo/features": "workspace:*",
            "@repo/api": "workspace:*",
        },
        "devDependencies": {
            "@types/react": "^19",
            "typescript": "^5",
            "vitest": "^3",
            "tailwindcss": "^4",
            "@repo/config": "workspace:*",
        },
    }, indent=2))

    _write(app_dir / "app.json", json.dumps({
        "expo": {
            "name": name,
            "slug": name,
            "version": "1.0.0",
            "scheme": name,
            "platforms": ["ios", "android"],
            "plugins": ["expo-router"],
            "updates": {"url": f"https://u.expo.dev/{name}"},
        },
    }, indent=2))

    _write(app_dir / "tsconfig.json", json.dumps({
        "extends": "expo/tsconfig.base",
        "compilerOptions": {"strict": True},
    }, indent=2))

    # Expo Router app dir
    app_routes = app_dir / "app"
    app_routes.mkdir(exist_ok=True)

    _write(app_routes / "_layout.tsx", """import { Stack } from 'expo-router'

export default function RootLayout() {
  return <Stack />
}
""")

    _write(app_routes / "index.tsx", """import { View, Text } from 'react-native'

export default function HomeScreen() {
  return <View><Text>Home — replace with @repo/features page</Text></View>
}
""")

    # Maestro
    maestro_dir = app_dir / "maestro" / "flows"
    maestro_dir.mkdir(parents=True, exist_ok=True)

    _write(maestro_dir / "smoke.yaml", f"""appId: com.{name}
---
- launchApp
- assertVisible: "Home"
- takeScreenshot: "smoke-home"
""")

    console.print(f"[green]✓ Mobile app '{name}' added[/green]")


# ---------------------------------------------------------------------------
# Package scaffolds
# ---------------------------------------------------------------------------

def _scaffold_ui_package(pkg_dir: Path) -> None:
    """Crée le package UI avec les primitives de base."""
    pkg_dir.mkdir(parents=True, exist_ok=True)
    src = pkg_dir / "src"

    for subdir in ["primitives", "layout", "components", "patterns", "blocks", "tokens"]:
        (src / subdir).mkdir(parents=True, exist_ok=True)

    _write(pkg_dir / "package.json", json.dumps({
        "name": "@repo/ui",
        "version": "0.1.0",
        "main": "src/index.ts",
        "types": "src/index.ts",
        "scripts": {"test": "vitest run", "build": "tsc"},
        "dependencies": {
            "react": "^19",
            "react-native": "~0.79",
            "nativewind": "^4",
            "@gluestack-ui/nativewind-utils": "^1",
            "lucide-react-native": "^0.400",
        },
        "devDependencies": {"typescript": "^5", "vitest": "^3"},
    }, indent=2))

    _write(src / "index.ts", """// Primitives
export { Box } from './primitives/box'
export { Typography } from './primitives/typography'

// Layout
export { Grid, GridItem } from './layout/grid'
export { Row } from './layout/row'
export { Column } from './layout/column'

// Blocks
export { BlockRenderer, createBlockRenderer } from './blocks/block-renderer'
""")

    # Box primitive
    _write(src / "primitives" / "box.tsx", """import { View } from 'react-native'
import type { ViewProps } from 'react-native'

export function Box({ className, ...props }: ViewProps & { className?: string }) {
  return <View className={className} {...props} />
}
""")

    # Typography primitive
    _write(src / "primitives" / "typography.tsx", """import { Text } from 'react-native'
import type { TextProps } from 'react-native'

type Variant = 'h1' | 'h2' | 'h3' | 'h4' | 'body' | 'body-sm' | 'caption' | 'label'

const VARIANT_CLASSES: Record<Variant, string> = {
  h1: 'text-4xl font-bold',
  h2: 'text-3xl font-semibold',
  h3: 'text-2xl font-semibold',
  h4: 'text-xl font-medium',
  body: 'text-base',
  'body-sm': 'text-sm',
  caption: 'text-xs text-gray-500',
  label: 'text-sm font-medium',
}

interface TypographyProps extends TextProps {
  variant?: Variant
  className?: string
}

export function Typography({ variant = 'body', className = '', ...props }: TypographyProps) {
  return <Text className={`${VARIANT_CLASSES[variant]} ${className}`} {...props} />
}
""")

    _write(src / "primitives" / "index.ts", "export { Box } from './box'\nexport { Typography } from './typography'\n")
    _write(src / "layout" / "index.ts", "// Grid, Row, Column — to be implemented\n")
    _write(src / "components" / "index.ts", "// Button, Input, etc. — to be implemented\n")
    _write(src / "patterns" / "index.ts", "// FormBuilder, TablePage — to be implemented\n")
    _write(src / "blocks" / "index.ts", "// BlockRenderer — to be implemented\n")
    _write(src / "tokens" / "index.ts", "// Design tokens — to be implemented\n")


def _scaffold_features_package(pkg_dir: Path) -> None:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    src = pkg_dir / "src"
    src.mkdir(exist_ok=True)

    _write(pkg_dir / "package.json", json.dumps({
        "name": "@repo/features",
        "version": "0.1.0",
        "main": "src/index.ts",
        "types": "src/index.ts",
        "scripts": {"test": "vitest run"},
        "dependencies": {
            "react": "^19",
            "@tanstack/react-query": "^5",
            "@repo/ui": "workspace:*",
            "@repo/api": "workspace:*",
        },
        "devDependencies": {"typescript": "^5", "vitest": "^3"},
    }, indent=2))

    _write(src / "index.ts", "// Features barrel — export feature pages here\n")


def _scaffold_api_package(pkg_dir: Path) -> None:
    pkg_dir.mkdir(parents=True, exist_ok=True)

    _write(pkg_dir / "package.json", json.dumps({
        "name": "@repo/api",
        "version": "0.1.0",
        "main": "src/index.ts",
        "types": "src/index.ts",
        "dependencies": {"convex": "^1"},
        "devDependencies": {"typescript": "^5"},
    }, indent=2))

    convex_dir = pkg_dir / "convex"
    convex_dir.mkdir(exist_ok=True)

    _write(convex_dir / "schema.ts", """import { defineSchema, defineTable } from 'convex/server'
import { v } from 'convex/values'

export default defineSchema({
  // Force update table (required)
  appVersions: defineTable({
    appName: v.string(),
    platform: v.string(),
    minVersion: v.string(),
    latestVersion: v.string(),
    storeUrl: v.optional(v.string()),
    releaseNotes: v.optional(v.string()),
    forceUpdate: v.boolean(),
  }).index('by_app_platform', ['appName', 'platform']),
})
""")

    (pkg_dir / "src").mkdir(exist_ok=True)
    _write(pkg_dir / "src" / "index.ts", "export {} // API barrel\n")


def _scaffold_config_package(pkg_dir: Path) -> None:
    pkg_dir.mkdir(parents=True, exist_ok=True)

    _write(pkg_dir / "package.json", json.dumps({
        "name": "@repo/config",
        "version": "0.1.0",
        "files": ["*.json"],
    }, indent=2))

    _write(pkg_dir / "tsconfig.base.json", json.dumps({
        "compilerOptions": {
            "target": "ES2022", "module": "ESNext", "moduleResolution": "bundler",
            "jsx": "react-jsx", "strict": True, "esModuleInterop": True,
            "skipLibCheck": True, "forceConsistentCasingInFileNames": True,
            "resolveJsonModule": True, "isolatedModules": True,
            "declaration": True, "declarationMap": True, "sourceMap": True,
        },
    }, indent=2))

    _write(pkg_dir / "tsconfig.web.json", json.dumps({
        "extends": "./tsconfig.base.json",
        "compilerOptions": {"lib": ["DOM", "DOM.Iterable", "ES2022"]},
    }, indent=2))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    """Écrit un fichier seulement s'il n'existe pas déjà."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def install_deps(workdir: Path) -> bool:
    """Lance pnpm install dans le monorepo."""
    console.print("[blue]Installing dependencies...[/blue]")
    try:
        proc = subprocess.run(
            ["pnpm", "install"],
            cwd=str(workdir),
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode == 0:
            console.print("[green]✓ Dependencies installed[/green]")
            return True
        console.print(f"[red]pnpm install failed: {proc.stderr[:500]}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]pnpm install error: {e}[/red]")
        return False
