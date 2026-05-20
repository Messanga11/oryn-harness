# oryn-harness

Long-running autonomous coding harness wrapping Claude Code CLI.
Pattern Planner → Generator ⇄ Evaluator inspiré du talk Anthropic "Building agents that run for hours".

**Multi-app monorepo support** : construit des projets avec plusieurs apps (web + mobile) dans un monorepo Turborepo avec composants universels partagés.

## Architecture

```
prompt utilisateur
       ↓
   [Researcher] ──→  .oryn/references/ (screenshots + design brief)
       ↓              (cherche des apps similaires en ligne)
   [Planner]    ──→  spec.md + apps.json + feature_list.json
       ↓              (décompose en monorepo multi-app + sprints)
   [LibManager] ──→  packages/ui/ (clone/crée @oryn/ui repo GitHub)
       ↓              (component library avec blocks CMS)
   ┌──────────────────────────────────────────────┐
   │ Pour chaque sprint :                          │
   │                                               │
   │   [Generator] ⇄ [Evaluator]                   │
   │     1. négocient contract.md                  │
   │     2. Generator code (packages/ui/,          │
   │        packages/features/, apps/)             │
   │     3. Evaluator teste :                      │
   │        - Vitest (unit)                        │
   │        - Playwright (E2E web)                 │
   │        - Maestro (E2E mobile)                 │
   │        - Lighthouse (perf/a11y)               │
   │        - npm audit + ZAP (sécurité)           │
   │     4. note + critique (6 axes)               │
   │     5. loop jusqu'à PASS ou restart           │
   └──────────────────────────────────────────────┘
       ↓
   monorepo multi-app livré
```

## Stack imposée

| Layer | Web | Mobile | Partagé |
|-------|-----|--------|---------|
| Framework | TanStack Start | Expo + Expo Router | — |
| UI | Gluestack UI + NativeWind | Gluestack UI + NativeWind | packages/ui/ |
| CMS/Backend | Vex CMS (Convex) | Vex CMS (Convex) | packages/api/ |
| State | TanStack Query | TanStack Query | packages/features/ |
| Auth | Better Auth | Better Auth | packages/api/ |
| Grid | 12 colonnes (CSS Grid) | 6 colonnes (Flexbox) | packages/ui/primitives/ |
| Forms | FormBuilder (TanStack Form + Zod) | FormBuilder | packages/ui/patterns/ |
| Tables | TablePage (TanStack Table) | TablePage | packages/ui/patterns/ |

## Component Library (@oryn/ui)

La component library est un **repo GitHub séparé** (`Messanga11/oryn-ui`) cloné dans `packages/ui/`.

**Règle absolue** : ZÉRO `<div>`, `<View>`, `<p>`, `<h1>` dans le code app. Tout passe par la lib.

| HTML/RN | @oryn/ui |
|---------|----------|
| `<div>` / `<View>` | `<Box>`, `<Column>`, `<Row>`, `<Grid>` |
| `<p>`, `<h1>`...`<h6>` | `<Typography variant="body\|h1\|h2...">` |
| `<img>` | `<Image>` |
| `<button>` | `<Button>`, `<IconButton>` |
| `<input>` | `<Input>`, `<TextArea>`, `<Select>` |
| `<ScrollView>` | `<ScrollContainer>` |
| `<FlatList>` | `<List>` (FlashList mobile) |

### CMS-driven Blocks

Les blocks Vex CMS sont mappés vers des composants via le `BlockRenderer` :
- Ajouter un block côté Vex → il est rendu automatiquement sur web ET mobile
- Blocks : HeroSection, FeatureSection, CtaSection, PricingSection, TestimonialsSection, FaqSection, etc.
- `createBlockRenderer(customBlocks)` pour étendre avec des blocks custom

### Auto-update
Si un composant manque, le Generator le crée dans la lib et `git push`. Toutes les apps l'utilisent immédiatement.

## Monorepo structure

```
project/
├── turbo.json
├── packages/
│   ├── ui/                    # Component library (REPO GITHUB @oryn/ui)
│   │   ├── primitives/        # Box, Typography, Image, Pressable, Spacer
│   │   ├── layout/            # Grid (12/6), Row, Column, PageLayout
│   │   ├── components/        # Button, Input, Card, Modal, Sheet...
│   │   ├── patterns/          # FormBuilder, TablePage, DetailPage
│   │   ├── blocks/            # CMS blocks + BlockRenderer
│   │   └── tokens/            # Design tokens
│   ├── features/              # Feature-based shared logic
│   │   └── <domain>/
│   │       ├── components/
│   │       ├── hooks/
│   │       └── services/
│   ├── api/                   # Convex + Vex collections
│   └── config/                # Shared config
├── apps/
│   ├── web/                   # TanStack Start
│   └── mobile/                # Expo (ou client-app/, driver-app/)
└── tests/
    └── load/                  # k6 scripts
```

## Testing pipeline

| Type | Outil | Cible |
|------|-------|-------|
| Unit | Vitest | hooks, services, utils |
| Integration | Testing Library | components + hooks |
| E2E web | Playwright | flows utilisateur web |
| E2E mobile | Maestro (YAML) | flows utilisateur mobile |
| Performance | Lighthouse CI | Core Web Vitals, a11y |
| Load | k6 | API endpoints |
| Sécurité | npm audit + OWASP ZAP + gitleaks | deps, app, secrets |

## Principes clés

1. **Adversarial pressure** : Generator et Evaluator ont des prompts opposés
2. **Contrats négociés** : avant de coder, les deux agents s'accordent sur "done"
3. **Rubric 6 axes** : design, originality, craft, functionality, tests, security
4. **Multi-app** : le Planner décompose en N apps partageant une component library
5. **Universal components** : même code React + React Native via Gluestack/NativeWind
6. **Feature-based** : packages/features/ partagé entre apps web et mobile
7. **Reset > patch** : si bloqué, jeter et recommencer

## Install

```bash
cd oryn-harness
pip install -e .
# Claude Code CLI
npm install -g @anthropic-ai/claude-code
# Playwright
pip install playwright && playwright install chromium
# Maestro (mobile E2E)
curl -Ls https://get.maestro.mobile.dev | bash
# Lighthouse CI
npm install -g @lhci/cli lighthouse
# k6 (load testing)
brew install k6
```

## Usage

```bash
# Simple : une seule app
oryn build "construis un éditeur de pixel art retro"

# Multi-app : spécifier les apps
oryn build "construis un uber-like" \
  --app "client-app:mobile:App client pour commander" \
  --app "driver-app:mobile:App chauffeur" \
  --app "admin:web:Dashboard admin"

# Config custom
oryn build "..." --workdir ./my-app --budget-usd 100 --max-iterations 30

# Sans design research (plus rapide)
oryn build "..." --no-design-research

# Stack overrides
oryn build "..." --web-framework tanstack-start --cms vex

# Status d'un run
oryn status --workdir ./my-app

# Reprendre un run
oryn resume --workdir ./my-app
```

## Structure du harness

```
oryn_harness/
├── config.py              # Config (stack, tests, apps, component lib)
├── state.py               # FS state (progress, apps, test_results)
├── claude_runner.py       # Wrapper subprocess Claude Code CLI
├── prompts.py             # System prompts + stack/testing/block knowledge
├── design_research.py     # Agent 0 : recherche de références design
├── component_library.py   # Agent 0.5 : gestion repo GitHub @oryn/ui
├── planner.py             # Agent 1 : décompose en monorepo multi-app + sprints
├── contract.py            # Négociation Generator ⇄ Evaluator
├── generator.py           # Agent 2 : builder (crée dans la lib + push)
├── evaluator.py           # Agent 3 : critic + test pipeline + archi check
├── playwright_runner.py   # Scripts Playwright (check + screenshot)
├── test_pipeline.py       # Scripts tests (Maestro, Lighthouse, k6, sécurité)
├── loop.py                # Orchestrateur principal
└── cli.py                 # Point d'entrée CLI
```
