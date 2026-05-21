"""Guides de coding injectés dans le workdir pour que les agents les lisent.

Au lieu de tout mettre dans les prompts (trop long, pollue le contexte),
on écrit des fichiers .md dans .oryn/guides/ que les agents DOIVENT lire.
"""
from __future__ import annotations

from pathlib import Path

CODING_PATTERNS_GUIDE = """# Coding Patterns Guide — Standards Senior Dev

Ce guide DOIT être suivi à la lettre. Lis-le AVANT de coder chaque sprint.

## 1. Architecture Composants

### Compound Components (pour les composants complexes)
```tsx
const TabsContext = createContext<TabsState | null>(null)

function Tabs({ children, defaultValue }: TabsProps) {
  const [active, setActive] = useState(defaultValue)
  return (
    <TabsContext value={{ active, setActive }}>
      {children}
    </TabsContext>
  )
}

function useTabsContext() {
  const ctx = useContext(TabsContext)
  if (!ctx) throw new Error('Must be used within <Tabs>')
  return ctx
}

Tabs.List = ({ children }) => <Row>{children}</Row>
Tabs.Trigger = ({ value, children }) => {
  const { active, setActive } = useTabsContext()
  return <Pressable onPress={() => setActive(value)}>{children}</Pressable>
}
Tabs.Content = ({ value, children }) => {
  const { active } = useTabsContext()
  return active === value ? <>{children}</> : null
}
```

### Error Boundary Hierarchy
```tsx
<ErrorBoundary fallback={<AppCrashScreen />}>           {/* app-level */}
  <ErrorBoundary fallback={<SectionError />}>            {/* section-level */}
    <Suspense fallback={<SectionSkeleton />}>
      {items.map(item => (
        <ErrorBoundary key={item.id} fallback={<CardError />}>  {/* item-level */}
          <Card data={item} />
        </ErrorBoundary>
      ))}
    </Suspense>
  </ErrorBoundary>
</ErrorBoundary>
```

### Suspense + Transitions (pas de flash de spinner)
```tsx
// Garder le contenu stale visible pendant le chargement
function navigate(url: string) {
  startTransition(() => setPage(url))
}

// Search avec dimming du contenu stale
const deferredQuery = useDeferredValue(query)
<Box style={{ opacity: query !== deferredQuery ? 0.5 : 1 }}>
  <Suspense fallback={<Spinner />}>
    <SearchResults query={deferredQuery} />
  </Suspense>
</Box>
```

## 2. Performance — Decision Tree (dans cet ordre)

1. Move state DOWN au plus petit sous-arbre qui en a besoin
2. Children-as-props — passer les sous-arbres coûteux comme `children`
3. Split contexts par concern (theme / auth / locale)
4. Virtualiser les listes > 50 items (FlashList mobile, react-window web)
5. `useMemo` SEULEMENT pour les computations coûteuses
6. `useCallback` SEULEMENT quand passé à un enfant `React.memo`
7. `useTransition` / `useDeferredValue` pour les updates non-urgentes
8. `React.lazy` + `Suspense` pour le code splitting par route
9. `React.memo` pour les items de liste avec props stables

### INP < 200ms
```ts
// Séparer le travail critique du non-critique
function handleInput(e) {
  updateUI(e.target.value)                    // critique — fait en premier
  requestAnimationFrame(() => {
    setTimeout(() => {                         // cède au browser pour peindre
      saveToServer(e.target.value)             // non-critique — après le paint
      updateWordCount(e.target.value)
    }, 0)
  })
}
```

### LCP < 2.5s
- Preload hero image avec `fetchpriority="high"`
- JAMAIS lazy-load l'image LCP
- `fetchpriority="low"` pour les images below-fold

### CLS < 0.1
- Réserver l'espace pour le contenu dynamique (aspect-ratio, min-height)
- Utiliser CSS transforms au lieu de propriétés qui triggèrent le layout

### React Native
- TOUTES les animations via Reanimated (UI thread worklets)
- `InteractionManager.runAfterInteractions()` pour le travail lourd
- FlashList au lieu de FlatList (recycling, 60 FPS)
- `expo-image` avec dimensions + placeholder

## 3. Caching — TanStack Query

### Query Key Factory (colocalisé par feature)
```ts
// features/todos/queries.ts
export const todoKeys = {
  all:     ['todos'] as const,
  lists:   ()              => [...todoKeys.all, 'list'] as const,
  list:    (filters: string) => [...todoKeys.lists(), { filters }] as const,
  details: ()              => [...todoKeys.all, 'detail'] as const,
  detail:  (id: number)    => [...todoKeys.details(), id] as const,
}
```

### Defaults agressifs
```ts
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5,   // 5 min fresh
      gcTime:    1000 * 60 * 10,   // 10 min en cache
      retry: 3,
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 30_000),
    },
  },
})
```

### JAMAIS copier les données de query dans du state local
```tsx
// ❌ MAUVAIS — casse le background sync
const { data } = useQuery(...)
const [todos, setTodos] = useState(data)

// ✅ BON — utiliser directement
const { data: todos } = useQuery(...)
```

### Optimistic Updates
```ts
useMutation({
  mutationFn: updateTodo,
  onMutate: async (newTodo) => {
    await queryClient.cancelQueries({ queryKey: todoKeys.all })
    const previous = queryClient.getQueryData(todoKeys.all)
    queryClient.setQueryData(todoKeys.all, old => [...old, newTodo])
    return { previous }
  },
  onError: (_err, _vars, context) => {
    queryClient.setQueryData(todoKeys.all, context.previous)  // rollback
  },
  onSettled: () => {
    queryClient.invalidateQueries({ queryKey: todoKeys.all })  // refetch la vérité
  },
})
```

### Prefetch on hover/focus
```tsx
<Pressable
  onHoverIn={() => queryClient.prefetchQuery({
    queryKey: todoKeys.detail(id),
    queryFn: () => fetchTodo(id),
    staleTime: 60_000,
  })}
>
```

## 4. Data Fetching

### Prévenir les waterfalls
- Prefetch dans le loader de route (block sur le critique, prefetch le secondaire)
- Prefetch dans la queryFn elle-même pour les données dépendantes
- Prefetch on hover/focus pour les liens

### Pagination — cursor-based
```ts
useInfiniteQuery({
  queryKey: ['feed'],
  queryFn: ({ pageParam }) => fetchFeed({ cursor: pageParam }),
  getNextPageParam: (lastPage) => lastPage.nextCursor,
  initialPageParam: undefined,
})
```

## 5. Error Handling

### Layered
| Type | Handler | UX |
|------|---------|-----|
| 5xx | ErrorBoundary (throwOnError) | Fallback section |
| 4xx validation | Local isError | Inline field errors |
| Background refetch | QueryCache onError | Toast |
| Network | fetchStatus === 'paused' | Offline banner |

### Error Codes (pas de strings)
```ts
const ERROR_CODES = {
  NETWORK_TIMEOUT: 'ERR_NETWORK_TIMEOUT',
  VALIDATION_FAILED: 'ERR_VALIDATION_FAILED',
  UNAUTHORIZED: 'ERR_UNAUTHORIZED',
} as const

class AppError extends Error {
  constructor(public readonly code: keyof typeof ERROR_CODES, message?: string) {
    super(message ?? ERROR_CODES[code])
    this.name = 'AppError'
  }
}
```

## 6. Sécurité

### Zod à CHAQUE frontière
```ts
const UserSchema = z.object({
  id: z.string().uuid(),
  email: z.string().email(),
  role: z.enum(['admin', 'user']),
})

// Parse à la frontière API
const result = UserSchema.safeParse(response)
if (!result.success) throw new AppError('VALIDATION_FAILED')
```

### Auth
- Web : httpOnly, Secure, SameSite=Strict cookies
- Mobile : expo-secure-store / Keychain (JAMAIS AsyncStorage)
- Access tokens courts (15min) + refresh tokens longs (httpOnly cookie)

## 7. Testing — Testing Trophy

| Niveau | Ratio | Quoi | Outil |
|--------|-------|------|-------|
| Static | Base | Types + lint | TypeScript strict, Biome |
| Unit | Petit | Pure functions, utils | Vitest |
| Integration | **LE PLUS GROS** | Composants + hooks + services | Testing Library + MSW |
| E2E | Petit | Parcours critiques seulement | Playwright / Maestro |

### MSW pour mocker les API
```ts
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'

const handlers = [
  http.get('/api/todos', () => HttpResponse.json([
    { id: '1', text: 'Learn MSW', done: false },
  ])),
]

const server = setupServer(...handlers)
beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
```

### Anti-patterns de test
- ❌ Snapshot d'un arbre complet (brittle, diffs incompréhensibles)
- ❌ Tester l'implémentation (vérifie que setState a été appelé)
- ✅ Tester le comportement (l'utilisateur voit X, clique Y, voit Z)
- ✅ Arrangement/Act/Assert clair

## 8. Organisation

### AHA > DRY
Avoid Hasty Abstractions. Dupliquer jusqu'à la 3e occurrence.
3 lignes similaires > 1 abstraction prématurée.

### Barrel exports — contrôlés
- Barrel-export SEULEMENT à la frontière de la feature (`features/todos/index.ts`)
- JAMAIS de barrel à l'intérieur d'une feature (circular deps, tree-shaking cassé)
- Imports internes = chemins directs

### Feature folder
```
features/todos/
  components/todo-list/index.tsx
  components/todo-list/todo-list.test.tsx
  hooks/use-todo-filters/index.ts
  services/use-get-todos.service.ts
  queries.ts          # key factory + query hooks
  todo.types.ts       # Zod schemas + inferred types
  index.ts            # barrel export (seul fichier qui re-export)
```
"""

UI_UX_GUIDE = """# UI/UX Quality Guide — Standards Designer Senior

## États obligatoires pour CHAQUE écran
Tout écran/composant qui charge des données DOIT gérer ces 4 états :
1. **Loading** — Skeleton animé (pas spinner), même layout que le contenu final
2. **Data** — Le contenu rendu normalement
3. **Empty** — Illustration (undraw) + message + CTA. PAS un texte "Aucun résultat"
4. **Error** — Illustration d'erreur + message compréhensible + bouton "Réessayer"

## Hiérarchie visuelle
- UN seul élément dominant par écran (titre principal, hero, CTA principal)
- Contraste : texte primaire vs secondaire vs tertiaire (100% → 70% → 50% opacity)
- Espace blanc généreux : le contenu respire (min 24px entre les sections)

## Touch targets
- Minimum 44x44px pour TOUT élément interactif (boutons, liens, icônes cliquables)
- Espacement minimum 8px entre les touch targets
- Zone de tap plus grande que la zone visuelle si nécessaire (padding)

## Micro-interactions
- Feedback immédiat sur CHAQUE action utilisateur
- Bouton : état pressed (scale 0.97), loading (spinner interne), disabled
- Toggle/switch : animation fluide (Reanimated, 200ms)
- Pull-to-refresh sur les listes
- Haptic feedback sur mobile (boutons principaux, toggle, delete)

## Typographie
- Max 2 fonts : une pour les headings, une pour le body (ou la même avec des weights différents)
- Tailles : 12 / 14 / 16 / 18 / 20 / 24 / 28 / 32 / 40
- Line-height : 1.2 headings, 1.5 body, 1.6 long text
- Letter-spacing : -0.02em headings, 0 body, 0.05em overline/label

## Couleurs
- Palette de 5-7 couleurs max (background, surface, primary, secondary, accent, error, success)
- Dark mode : pas juste inverser — surface légèrement élevée (#1a1a1a, #242424, #2e2e2e)
- Contraste WCAG AA : 4.5:1 texte normal, 3:1 grand texte
- Sémantique : rouge = destructif, vert = succès, jaune = warning, bleu = info

## Icônes
- UNE seule lib : Lucide React (lucide-react-native)
- Tailles cohérentes : 16px inline, 20px nav, 24px actions, 32px illustrations
- Stroke width cohérent : 1.5px pour tout (default Lucide)
- Couleur : hérite du texte parent, jamais hardcodée

## Illustrations
- undraw.co pour les illustrations de page (empty states, onboarding, 404, etc.)
- Style cohérent avec la palette du projet (changer les couleurs undraw)
- SVG embedé, pas images raster
- Taille : 200-280px pour les empty states, 320-400px pour les pages complètes

## Animations
- 200ms pour les transitions simples (hover, focus, toggle)
- 300ms pour les transitions de layout (expand, collapse)
- 500ms pour les entrées d'écran (fade in, slide up)
- ease-out pour les entrées, ease-in pour les sorties
- JAMAIS d'animation qui bloque l'interaction

## Responsive
- Mobile-first pour le mobile, desktop-first pour le web
- Breakpoints : 375 (mobile), 768 (tablet), 1024 (desktop), 1440 (wide)
- Pas de scroll horizontal
- Navigation : bottom tabs mobile, sidebar web
- Grid : 6 cols mobile, 12 cols web

## Forms
- Label au-dessus de l'input (pas placeholder-as-label)
- Validation en temps réel (onBlur), pas seulement au submit
- Messages d'erreur sous le champ, en rouge, spécifiques (pas "champ invalide")
- Bouton submit disabled tant que le form n'est pas valid
- Loading state sur le bouton submit pendant l'envoi
- Success feedback (toast ou redirect) après soumission réussie
"""

DESIGN_SYSTEM_GUIDE = """# Design System Compliance Guide

## Règle #1 : ZÉRO primitives HTML/RN
ESLint est configuré pour BLOQUER :
- `<div>` → `<Box>` from @repo/ui
- `<span>` → `<Box>` or `<Typography>`
- `<p>` → `<Typography variant="body">`
- `<h1>`...`<h6>` → `<Typography variant="h1">`...`<Typography variant="h6">`
- `<button>` → `<Button>` from @repo/ui
- `<input>` → `<Input>` from @repo/ui
- `<textarea>` → `<TextArea>` from @repo/ui
- `<img>` → `<Image>` from @repo/ui
- `<a>` → `<Link>` from @repo/ui ou router Link
- `<ul>/<li>` → `<List>/<ListItem>` from @repo/ui

Exception : `packages/ui/` lui-même (le design system) peut utiliser les primitives.

## Règle #2 : Pas de styles inline
```tsx
// ❌ MAUVAIS
<Box style={{ padding: 16, backgroundColor: '#f0f0f0' }}>

// ✅ BON
<Box className="p-4 bg-gray-100">
```

## Règle #3 : Pas de couleurs hardcodées
```tsx
// ❌ MAUVAIS
<Typography className="text-[#3b82f6]">

// ✅ BON — utiliser les tokens Tailwind
<Typography className="text-primary">
```

## Règle #4 : Composants composés
Chaque composant complexe doit suivre le pattern compound :
```tsx
<Card>
  <Card.Header>
    <Card.Title>Titre</Card.Title>
    <Card.Description>Description</Card.Description>
  </Card.Header>
  <Card.Content>...</Card.Content>
  <Card.Footer>...</Card.Footer>
</Card>
```

## Règle #5 : Accessibilité
- Tout élément interactif a un `accessibilityLabel` (mobile) ou `aria-label` (web)
- Les images ont un `alt` text
- Les formulaires ont des `<label>` associés aux inputs
- Le focus order est logique (tab navigation)
- Les couleurs ne sont jamais le SEUL moyen de transmettre l'information
"""


def install_guides(workdir: Path) -> None:
    """Installe les guides de coding dans .oryn/guides/."""
    guides_dir = workdir / ".oryn" / "guides"
    guides_dir.mkdir(parents=True, exist_ok=True)

    (guides_dir / "coding-patterns.md").write_text(CODING_PATTERNS_GUIDE)
    (guides_dir / "ui-ux-quality.md").write_text(UI_UX_GUIDE)
    (guides_dir / "design-system-compliance.md").write_text(DESIGN_SYSTEM_GUIDE)
