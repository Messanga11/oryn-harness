"""Gestion de la component library comme repo GitHub séparé.

La component library est le coeur du système. C'est un repo GitHub
indépendant qui contient TOUS les composants UI (primitives, composants,
patterns, blocks). Aucun `<div>`, `<View>`, `<p>`, `<h1>` n'apparaît
dans le code des apps — tout passe par la lib.

Le harness peut :
1. Cloner/créer le repo si inexistant
2. L'intégrer dans le monorepo comme package
3. Ajouter de nouveaux composants quand nécessaire
4. Push les changements sur GitHub

Les blocks Vex CMS sont mappés vers des composants de la lib via un
BlockRenderer universel. Ajouter un block côté backend = il est rendu
automatiquement sur web ET mobile.
"""
from __future__ import annotations

from rich.console import Console

from .claude_runner import ClaudeResult, ClaudeRunner
from .config import HarnessConfig
from .state import StateManager

console = Console()

# Prompt pour l'agent qui gère la component library
COMPONENT_LIBRARY_PROMPT = """Tu es LIBRARY_MANAGER, un agent spécialisé dans la gestion d'une component library universelle (React + React Native).

# Contexte
La component library est un REPO GITHUB SÉPARÉ qui contient TOUS les composants UI.
- Repo : $REPO
- Package name : $PACKAGE_NAME
- Base : Gluestack UI v2 (copy-paste) + NativeWind + shadcn patterns

# Règle absolue : ZÉRO primitives HTML/RN dans le code app
AUCUN `<div>`, `<View>`, `<Text>`, `<p>`, `<h1>`, `<span>`, `<img>`, `<button>`, `<input>`,
`<ScrollView>`, `<FlatList>` ne doit apparaître dans le code des apps ou des features.

TOUT passe par des composants de la library :
- `<div>` → `<Box>` ou `<Column>` ou `<Row>` ou `<Grid>`
- `<p>`, `<span>` → `<Typography variant="body">` ou `<Typography variant="caption">`
- `<h1>`...`<h6>` → `<Typography variant="h1">` ... `<Typography variant="h6">`
- `<img>` → `<Image>` (avec cache, dimensions, loading state)
- `<button>` → `<Button>` ou `<Pressable>` ou `<IconButton>`
- `<input>` → `<Input>` ou `<TextArea>` ou `<Select>` etc.
- `<ScrollView>` → `<ScrollContainer>`
- `<FlatList>` → `<List>` (FlashList sur mobile)
- Layouts → `<PageLayout>`, `<SectionLayout>`, `<CardLayout>`

# Structure de la library
```
oryn-ui/
├── package.json          # name: "@oryn/ui"
├── tsconfig.json
├── src/
│   ├── primitives/       # Briques de base universelles
│   │   ├── box/          # Wrapper universel (remplace div/View)
│   │   ├── typography/   # Texte typé (h1-h6, body, caption, label, overline)
│   │   ├── image/        # Image optimisée (expo-image sur mobile, next/image sur web)
│   │   ├── pressable/    # Zone cliquable avec feedback
│   │   ├── scroll-container/  # ScrollView abstrait
│   │   ├── list/         # FlashList/FlatList abstrait
│   │   ├── spacer/       # Espace adaptatif
│   │   └── divider/      # Séparateur
│   │
│   ├── layout/           # Système de layout
│   │   ├── grid/         # Grid 12 cols web / 6 cols mobile
│   │   ├── row/          # Flex row
│   │   ├── column/       # Flex column avec span
│   │   ├── page-layout/  # Layout de page (header, content, footer)
│   │   ├── section-layout/  # Section avec titre, description, contenu
│   │   └── card-layout/  # Card avec header, body, footer
│   │
│   ├── components/       # Composants UI composés
│   │   ├── button/
│   │   ├── icon-button/
│   │   ├── input/
│   │   ├── text-area/
│   │   ├── select/
│   │   ├── checkbox/
│   │   ├── switch/
│   │   ├── slider/
│   │   ├── radio-group/
│   │   ├── card/
│   │   ├── modal/
│   │   ├── drawer/
│   │   ├── sheet/
│   │   ├── toast/
│   │   ├── alert/
│   │   ├── alert-dialog/
│   │   ├── avatar/
│   │   ├── badge/
│   │   ├── chip/
│   │   ├── tabs/
│   │   ├── accordion/
│   │   ├── dropdown-menu/
│   │   ├── tooltip/
│   │   ├── skeleton/
│   │   ├── spinner/
│   │   ├── empty-state/
│   │   ├── error-state/
│   │   └── loading-state/
│   │
│   ├── patterns/         # Orchestrateurs headless
│   │   ├── form-builder/      # @messsanga11/formbuilder intégré
│   │   ├── table-page/        # @messsanga11/table-page intégré
│   │   ├── detail-page/       # Page de détail avec sections
│   │   ├── list-page/         # Page de liste avec filtres
│   │   ├── auth-form/         # Login/signup/forgot-password
│   │   └── update-gate/       # Force update check (comme WhatsApp)
│   │
│   ├── blocks/           # Composants CMS-driven (mappés aux blocks Vex)
│   │   ├── block-renderer/    # Le moteur de rendu universel
│   │   ├── hero-section/
│   │   ├── feature-section/
│   │   ├── cta-section/
│   │   ├── pricing-section/
│   │   ├── testimonials-section/
│   │   ├── faq-section/
│   │   ├── content-section/   # Rich text + images
│   │   ├── gallery-section/
│   │   ├── stats-section/
│   │   ├── team-section/
│   │   ├── contact-section/
│   │   └── custom-block/      # Fallback pour blocks inconnus
│   │
│   ├── tokens/           # Design tokens
│   │   ├── colors.ts
│   │   ├── spacing.ts
│   │   ├── typography.ts
│   │   ├── shadows.ts
│   │   ├── borders.ts
│   │   └── breakpoints.ts
│   │
│   └── index.ts          # Barrel export
└── README.md
```

# BlockRenderer — Le pattern clé
Le BlockRenderer est le composant qui fait le pont entre le CMS et le frontend :

```typescript
// blocks/block-renderer/index.tsx
import type { VexBlock } from '@oryn/ui/blocks/types'

// Registry : map blockType → React component
const BLOCK_REGISTRY: Record<string, React.ComponentType<any>> = {
  'hero': HeroSection,
  'features': FeatureSection,
  'cta': CtaSection,
  'pricing': PricingSection,
  'testimonials': TestimonialsSection,
  'faq': FaqSection,
  'content': ContentSection,
  'gallery': GallerySection,
  'stats': StatsSection,
  'team': TeamSection,
  'contact': ContactSection,
}

export function BlockRenderer({ blocks }: { blocks: VexBlock[] }) {
  return (
    <Column>
      {blocks.map((block, i) => {
        const Component = BLOCK_REGISTRY[block.blockType] ?? CustomBlock
        return <Component key={block.id ?? i} {...block} />
      })}
    </Column>
  )
}

// Extensible : les apps peuvent ajouter leurs propres blocks
export function createBlockRenderer(customBlocks: Record<string, React.ComponentType<any>>) {
  const registry = { ...BLOCK_REGISTRY, ...customBlocks }
  return function ExtendedBlockRenderer({ blocks }: { blocks: VexBlock[] }) {
    return (
      <Column>
        {blocks.map((block, i) => {
          const Component = registry[block.blockType] ?? CustomBlock
          return <Component key={block.id ?? i} {...block} />
        })}
      </Column>
    )
  }
}
```

# Chaque composant de block est entièrement customisable via Vex CMS
Les blocks ont des "fields" configurables dans Vex :
```typescript
// Exemple : HeroSection block definition dans Vex
defineBlock({
  slug: 'hero',
  fields: [
    { name: 'title', type: 'text', required: true },
    { name: 'subtitle', type: 'text' },
    { name: 'backgroundImage', type: 'upload' },
    { name: 'ctaText', type: 'text' },
    { name: 'ctaLink', type: 'text' },
    { name: 'alignment', type: 'select', options: ['left', 'center', 'right'] },
    { name: 'variant', type: 'select', options: ['dark', 'light', 'gradient'] },
  ],
})

// Le composant HeroSection reçoit ces fields comme props
export function HeroSection({ title, subtitle, backgroundImage, ctaText, ctaLink, alignment, variant }: HeroSectionProps) {
  return (
    <SectionLayout variant={variant} backgroundImage={backgroundImage}>
      <Column align={alignment}>
        <Typography variant="h1">{title}</Typography>
        {subtitle && <Typography variant="body-lg">{subtitle}</Typography>}
        {ctaText && <Button onPress={() => navigate(ctaLink)}>{ctaText}</Button>}
      </Column>
    </SectionLayout>
  )
}
```

# UpdateGate — Force update pattern (comme WhatsApp)
Toutes les apps DOIVENT wrapper leur root avec `<UpdateGate>`.
Quand une nouvelle version est release, les utilisateurs sur une ancienne
version voient un écran bloquant qui les force à mettre à jour.

## Comment ça fonctionne

### 1. Backend (Convex/Vex) — table `appVersions`
```typescript
// convex/schema.ts
appVersions: defineTable({
  appName: v.string(),          // "client-app", "driver-app", etc.
  platform: v.string(),         // "ios" | "android" | "web"
  minVersion: v.string(),       // Version minimale requise (ex: "2.1.0")
  latestVersion: v.string(),    // Dernière version disponible
  storeUrl: v.optional(v.string()),  // URL App Store / Play Store
  releaseNotes: v.optional(v.string()),
  forceUpdate: v.boolean(),     // true = bloquant, false = suggestion
  updatedAt: v.number(),
})

// convex/appVersions.ts — query publique
export const checkVersion = query({
  args: { appName: v.string(), platform: v.string(), currentVersion: v.string() },
  handler: async (ctx, { appName, platform, currentVersion }) => {
    const config = await ctx.db
      .query("appVersions")
      .withIndex("by_app_platform", (q) => q.eq("appName", appName).eq("platform", platform))
      .first()
    if (!config) return { updateRequired: false }
    const needsUpdate = semverLt(currentVersion, config.minVersion)
    return {
      updateRequired: needsUpdate && config.forceUpdate,
      updateSuggested: needsUpdate && !config.forceUpdate,
      latestVersion: config.latestVersion,
      storeUrl: config.storeUrl,
      releaseNotes: config.releaseNotes,
    }
  },
})
```

### 2. Composant UpdateGate (packages/ui/src/patterns/update-gate/)
```typescript
// patterns/update-gate/index.tsx
import { useQuery } from 'convex/react'
import { api } from '@repo/api'

interface UpdateGateProps {
  appName: string
  currentVersion: string       // depuis package.json ou app.json
  platform: 'ios' | 'android' | 'web'
  children: React.ReactNode
}

export function UpdateGate({ appName, currentVersion, platform, children }: UpdateGateProps) {
  const versionCheck = useQuery(api.appVersions.checkVersion, {
    appName,
    platform,
    currentVersion,
  })

  // Pendant le check : afficher l'app normalement (pas de flash)
  if (versionCheck === undefined) return <>{children}</>

  // Force update requis : bloquer l'app
  if (versionCheck.updateRequired) {
    return (
      <ForceUpdateScreen
        latestVersion={versionCheck.latestVersion}
        storeUrl={versionCheck.storeUrl}
        releaseNotes={versionCheck.releaseNotes}
        platform={platform}
      />
    )
  }

  // Update suggéré (non bloquant) : banner dismissable
  if (versionCheck.updateSuggested) {
    return (
      <UpdateSuggestionProvider
        latestVersion={versionCheck.latestVersion}
        storeUrl={versionCheck.storeUrl}
      >
        {children}
      </UpdateSuggestionProvider>
    )
  }

  return <>{children}</>
}
```

### 3. ForceUpdateScreen (plein écran, bloquant, pas de skip)
```typescript
// patterns/update-gate/force-update-screen.tsx
export function ForceUpdateScreen({ latestVersion, storeUrl, releaseNotes, platform }) {
  return (
    <PageLayout centered>
      <Column align="center" gap={24} padding={32}>
        <Image source={updateIllustration} size={200} />
        <Typography variant="h2" align="center">
          Mise à jour requise
        </Typography>
        <Typography variant="body" align="center" color="muted">
          Une nouvelle version ({latestVersion}) est disponible.
          Merci de mettre à jour pour continuer.
        </Typography>
        {releaseNotes && (
          <Card>
            <Typography variant="body-sm">{releaseNotes}</Typography>
          </Card>
        )}
        <Button
          size="lg"
          onPress={() => {
            if (platform === 'web') {
              window.location.reload()  // force reload pour récupérer le nouveau bundle
            } else {
              Linking.openURL(storeUrl)  // ouvre App Store / Play Store
            }
          }}
        >
          Mettre à jour
        </Button>
      </Column>
    </PageLayout>
  )
}
```

### 4. Usage dans chaque app (root layout)
```typescript
// apps/mobile/app/_layout.tsx
import { UpdateGate } from '@oryn/ui/patterns/update-gate'
import { version } from '../package.json'

export default function RootLayout() {
  return (
    <UpdateGate appName="client-app" currentVersion={version} platform={Platform.OS}>
      <ConvexProvider>
        <Stack />
      </ConvexProvider>
    </UpdateGate>
  )
}

// apps/web/src/routes/__root.tsx
import { UpdateGate } from '@oryn/ui/patterns/update-gate'
import { version } from '../../package.json'

export function RootLayout() {
  return (
    <UpdateGate appName="admin-web" currentVersion={version} platform="web">
      <Outlet />
    </UpdateGate>
  )
}
```

### 5. Web : auto-check via Service Worker (en plus)
Pour le web, en plus de l'UpdateGate, un Service Worker check les assets :
- Au build, un hash du bundle est généré dans `version.json`
- Le SW poll `version.json` toutes les 5 minutes
- Si le hash change → notification "Nouvelle version disponible" → hard reload

### 6. Mobile : expo-updates pour les OTA updates
- Les petits updates (JS-only) passent par `expo-updates` (OTA, pas besoin du store)
- Les gros updates (native changes) passent par le store → forceUpdate via la table Convex
- Au lancement : `Updates.checkForUpdateAsync()` + `Updates.fetchUpdateAsync()` + `Updates.reloadAsync()`

## IMPORTANT
- Le UpdateGate est OBLIGATOIRE dans toutes les apps
- Le Generator DOIT l'intégrer dans le root layout de chaque app
- Le backend DOIT avoir la table `appVersions` dans le schema Convex
- L'Evaluator DOIT vérifier que le force update est implémenté

# Quand ajouter un composant à la lib
Si le Generator a besoin d'un composant qui N'EXISTE PAS dans la lib :
1. Il le CRÉE dans la lib (pas dans le code app)
2. Il fait `git add + git commit + git push` sur le repo de la lib
3. Il met à jour le package dans le monorepo (`pnpm update @oryn/ui`)
4. Il utilise le nouveau composant dans le code app

JAMAIS de composants UI écrits directement dans apps/ ou packages/features/.
"""


class ComponentLibraryManager:
    """Gère le repo GitHub de la component library."""

    def __init__(self, config: HarnessConfig, state: StateManager):
        self.config = config
        self.state = state
        self.repo = config.stack.component_library_repo
        self.package_name = config.stack.component_library_package_name
        self.lib_dir = config.workdir / "packages" / "ui"

        self.runner = ClaudeRunner(
            cwd=config.workdir,
            model=config.generator_model,
            system_prompt=COMPONENT_LIBRARY_PROMPT.replace(
                "$REPO", self.repo,
            ).replace(
                "$PACKAGE_NAME", self.package_name,
            ),
            permission_mode=config.permission_mode,
            timeout=config.cli_timeout,
            allowed_tools=None,
        )

    def setup(self) -> ClaudeResult:
        """Clone ou crée le repo de la component library et l'intègre dans le monorepo."""
        console.rule("[bold blue]COMPONENT LIBRARY SETUP")

        prompt = f"""Setup la component library pour ce projet.

1. Vérifie si le repo `{self.repo}` existe sur GitHub avec `gh repo view {self.repo}`.
   - S'il existe : clone-le dans `packages/ui/` (ou `git subtree add` / `git submodule`)
   - S'il n'existe pas : crée-le avec `gh repo create {self.repo} --public` puis initialise-le

2. Si le repo est vide ou nouveau, initialise la structure :
   - package.json avec name: "{self.package_name}"
   - tsconfig.json
   - src/ avec la structure complète (primitives/, layout/, components/, patterns/, blocks/, tokens/)
   - Crée au minimum les primitives de base : Box, Typography, Image, Pressable, Spacer, Divider
   - Crée le layout system : Grid (12/6), Row, Column, PageLayout, SectionLayout, CardLayout
   - Crée le BlockRenderer avec au moins HeroSection et ContentSection

3. Intègre formbuilder depuis `{self.config.stack.form_builder_repo}` :
   - Clone le code source dans src/patterns/form-builder/
   - Adapte les imports pour utiliser les primitives de la lib (Box, Typography, etc.)

4. Intègre table-page depuis `{self.config.stack.table_page_repo}` :
   - Clone le code source dans src/patterns/table-page/
   - Adapte les imports pour utiliser les primitives de la lib

5. Configure le package dans le monorepo :
   - Ajoute dans pnpm-workspace.yaml si pas déjà fait
   - Vérifie que les autres packages peuvent importer depuis "{self.package_name}"

6. Commit et push sur GitHub

IMPORTANT : TOUS les composants doivent fonctionner sur web ET mobile (NativeWind + react-native-web).
Pas de <div>, <View>, <p>, <h1> directs — tout passe par Box, Typography, etc.
"""

        result = self.runner.run(prompt)
        self.state.write_trace("component_library_setup", result.raw_stdout)

        if result.success:
            console.print(f"[green]✓ Component library setup dans packages/ui/[/green]")
        else:
            console.print(f"[yellow]⚠ Component library setup partiel : {result.raw_stderr[:200]}[/yellow]")

        return result

    def add_component(self, component_name: str, component_type: str, description: str) -> ClaudeResult:
        """Ajoute un nouveau composant à la library et push sur GitHub.

        Args:
            component_name: Nom du composant (ex: "pricing-table")
            component_type: Type (primitives|layout|components|patterns|blocks)
            description: Ce que le composant doit faire
        """
        console.print(f"[blue]+ Adding {component_type}/{component_name} to component library[/blue]")

        prompt = f"""Ajoute un nouveau composant à la component library.

Composant : {component_name}
Type : {component_type}
Description : {description}

1. Crée le composant dans `packages/ui/src/{component_type}/{component_name}/`
   - index.tsx : composant universel (web + mobile)
   - {component_name}.test.tsx : tests Vitest
   - types.ts : types si nécessaire

2. Le composant DOIT :
   - Utiliser uniquement les primitives de la lib (Box, Typography, etc.)
   - Fonctionner sur web ET mobile (NativeWind)
   - Être entièrement customisable via props
   - Si c'est un block : être configurable depuis Vex CMS

3. Exporte-le depuis src/index.ts

4. Si c'est un block, ajoute-le au BLOCK_REGISTRY dans blocks/block-renderer/

5. Commit : `feat(ui): add {component_name} {component_type}`
6. Push : `git push` (dans le repo de la lib)

RAPPEL : zéro <div>, <View>, <p>, <h1> — tout passe par les primitives de la lib.
"""

        result = self.runner.run(prompt)
        self.state.write_trace(f"component_library_add_{component_name}", result.raw_stdout)

        return result

    def sync_to_monorepo(self) -> ClaudeResult:
        """Synchronise la dernière version de la lib dans le monorepo."""
        console.print("[blue]Syncing component library...[/blue]")

        prompt = f"""Synchronise la component library dans le monorepo.

1. Dans packages/ui/, fait `git pull` pour récupérer les derniers changements
2. Run `pnpm install` pour mettre à jour les dépendances
3. Vérifie que les imports depuis "{self.package_name}" fonctionnent dans les autres packages

Confirme que tout est synchronisé.
"""

        result = self.runner.run(prompt)
        return result
