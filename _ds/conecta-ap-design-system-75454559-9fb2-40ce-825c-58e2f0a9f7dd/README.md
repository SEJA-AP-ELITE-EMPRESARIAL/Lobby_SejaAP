# Conecta AP — Design System

**Conecta AP** é uma plataforma SaaS de **gestão estratégica empresarial** da **SEJA AP** (aka "Elite AP"). Permite a consultores, coordenadores e clientes acompanhar KPIs em 7 pilares, gerir hierarquias (Coordenador → Consultor → Empresa), integrar Power BI e executar Reuniões Mensais de Resultados (RMR) e Reuniões Semanais de Consultoria (RSC).

- **Marca comercial do produto:** Conecta AP
- **Empresa mãe:** SEJA AP (Elite AP é o nome legal/histórico)
- **Tagline:** "Sistema de Evolução Empresarial"
- **Idioma:** 🇧🇷 Português do Brasil (todo o produto)
- **Tom visual:** Premium, dourado sobre preto profundo, acabamento "executive dashboard"

---

## Fontes deste sistema (não assuma que o leitor tem acesso)

| Recurso | Caminho / Link |
|---|---|
| Frontend (SPA React + MUI 7) | `frontend-homologacao/frontend/` (mounted folder) |
| Backend (Django REST) | `backend-homologacao/backend/` (mounted folder) |
| UI_GUIDE.md oficial | `frontend-homologacao/frontend/UI_GUIDE.md` — v2.02, 2026-02 |
| Design tokens CSS | `frontend-homologacao/frontend/src/styles/tokens.css` |
| Tema MUI | `frontend-homologacao/frontend/src/theme/index.js` |
| APP_CONFIG | `frontend-homologacao/frontend/APP_CONFIG.json` |
| Documentação funcional | `frontend-homologacao/doc/documentacao-funcional-executiva (1).md` |

Nenhuma referência Figma foi fornecida. Esta base foi derivada do UI_GUIDE oficial v2.02 e de leitura direta do código.

---

## Produtos representados

Apenas **1 produto**, com **múltiplos portais baseados em hierarquia**:

| Portal | Rota raiz | Público |
|---|---|---|
| Visão Geral | `/visao-geral` | Admin / Consultor interno |
| Meu Dashboard | `/meu-dashboard` | Cliente externo, SuperAdmin, Gestão, Coordenador, Consultor |
| Portal do Cliente | `/cliente/*` | Cliente final (empresa contratante) |
| Portal do Consultor | `/consultor/*` | Consultor SEJA AP |
| Portal de Coordenação | `/coordenacao/*` | Coordenador |
| SuperAdmin | `/superadmin/*` | Admin de plataforma |

Módulos principais: **Dados, Relatórios Incorporados, Indicadores, Selo EAP, Pesquisa de Satisfação (interna/externa), Reuniões (RMR/RSC), Organograma, Controle de Jornada, Planejamento Estratégico**.

---

## Index — arquivos neste sistema

```
Conecta AP Design System/
├── README.md                ← você está aqui
├── SKILL.md                 ← prompt reutilizável para agentes (Claude Code compatível)
├── colors_and_type.css      ← tokens CSS (cores, tipografia, espaçamento, motion)
├── assets/                  ← logos SEJA AP, ELITE, favicon
├── preview/                 ← cards do Design System tab (registrados como assets)
└── ui_kits/
    └── conecta-ap/          ← recreation hi-fi do SPA (sidebar, header, KPIs, tabela,
                                login, RMR, etc) com index.html clickthru
```

---

## CONTENT FUNDAMENTALS

Português do Brasil, **você** (nunca "tu"), tom **profissional-corporativo-ameno**. A copy trata o usuário como executivo ocupado, não como usuário iniciante.

### Tom e voz
- **Direto e informativo**, sem superlativos de marketing dentro do produto.
- **Profissional-corporativo**: usa "gestão", "acompanhamento", "indicadores", "consultoria".
- **Calorosa apenas em entradas/saudações** — o dashboard abre com `{Bom dia|Boa tarde|Boa noite}, {nome}!` via `getGreeting()`.
- **Sem jargão casual**, sem gírias, sem "!!" múltiplos.

### Casing
- **Title Case ou Sentence case** em títulos de página: "Visão Geral", "Acesso Rápido", "Clientes Gerenciados pelo EAP"
- **Sentence case** em captions/descrições: "Em andamento", "Total cadastrado"
- **UPPERCASE** apenas em KPI labels (`.kpiLabel` — tracking 2px, peso 800)
- **Nunca** all-caps em parágrafos ou botões (`textTransform: none` é regra do tema MUI)

### Linguagem de microcópia (exemplos reais)
- "Visualize dados e métricas dos clientes"
- "Acompanhe indicadores de performance"
- "Em andamento" (status)
- "Entrando..." (loading CTA)
- "Bem-vindo de volta" (login)
- "Selecione uma empresa para ver o dashboard"
- "Escolha até {N} atalhos para fixar no Dashboard"

### O que NÃO fazer
- ❌ Não usar emoji como linguagem de UI (exceção rara: 🏢 em empty state do dashboard; 🔬 para debug mode — uso utilitário, não decorativo).
- ❌ Não abreviar termos de negócio ("RMR" e "RSC" são aceitos porque têm peso institucional; "Consult." não).
- ❌ Não usar "!" em mensagens informativas (reserve para saudação).
- ❌ Não usar "Clique aqui" como label de link.

### "Vibe"
Premium, sóbrio, consultoria executiva. Pense num painel de BI para diretoria — não num app consumer.

---

## VISUAL FOUNDATIONS

### Paleta
- **Primária (ouro):** `#C7A444` (hover `#C8A654`, light `#E8B84B`, dark `#9C7C21`). Usada em CTAs únicos, ícones ativos, KPI icon BG, scrollbar, active nav, divider.
- **Secundária (antracite):** `#4A4A49` neutra industrial.
- **Superfícies (dark padrão):** `#141412` (bg), `#1E1E1C` (surface), `#282826` (hover), `#323230` (elevated/stat cards), `#1A1A18` (alt/quick access).
- **Texto:** `#F0F0F0` / `#D0D0D0` / `#A0A0A0` (3 níveis).
- **Borders:** `rgba(175,144,61,0.3)` (dourado sutil) + `rgba(255,255,255,0.1)` (neutro).
- **Status:** `#28A745` / `#FFC107` / `#DC3545` / `#17A2B8` (Bootstrap-like).
- **Light theme** reduz ouro para `#B8952E` (mais legível em BG claro) e usa BG `#EFEDE8` (quase-papel warm off-white), NÃO branco puro.

### Tipografia
- **Família de títulos:** Montserrat 600 — h1-h6, botões, labels, overline
- **Família de corpo:** Roboto 400/500 — body, inputs, tabelas
- Escala MUI usa **`clamp()`** fluido (h1 = 2rem→3.5rem, body1 = 0.875rem→1rem)
- Pesos: 300/400/500/600/700 (sem 800/900 em texto — apenas em `.kpiValue` CSS)
- `letter-spacing: -0.015em` em h1, `0.04em` + semibold em botões, `0.08em` uppercase em overlines

### Espaçamento
- Base 4px; escala: 2, 4, 8, 12, 16, clamp(24–40), clamp(32–56)
- MUI `spacing = 8px` (sx={{ p: 2 }} = 16px)
- Content area padding `{ xs: 2, md: 3, xl: 4 }`, maxWidth 1920, centered

### Backgrounds
- **Sem imagens, sem ilustrações full-bleed** no produto — 100% cor sólida + gradientes radiais sutis.
- **Login** usa **2 blobs radiais** com parallax do mouse (gold at 7% opacity, blurred) + gradiente linear 160deg — efeito ambient glow, não wallpaper.
- Nenhum padrão repetido. Nenhum grão. Nenhuma textura.

### Animação
- **Curva padrão:** `cubic-bezier(0.4, 0, 0.2, 1)` (Material standard)
- **Entrada (decelerate):** `cubic-bezier(0, 0, 0.2, 1)` — cards sobem `translateY(20-28px)` + fade, duration 450-550ms
- **Stagger:** **60-90ms** entre cards, +**320ms offset** entre grupos
- **Keyframes oficiais:** `slideUpFade`, `authCardIn`, `alertSlideDown`, `successPopIn`, `glow-pulse`, `count-in`, `stripe-shimmer`
- **Nunca `transition: all`** — sempre propriedades explícitas (`transform`, `box-shadow`, `border-color`)
- Respeita `prefers-reduced-motion`

### Hover
- Cards: `translateY(-4px) scale(1.02)` + elevação sobe 1 nível + `--glow-primary` + borda passa a `rgba(199,164,68,0.5)`
- KPI icon on card-hover: `rotate(-10deg) scale(1.14)`
- Botões: não usar elevação (`disableElevation: true`); em CTA gradient: `translateY(-2px)` + sombra intensifica
- Links: `color: var(--primary-hover)` + `underline` em auth links

### Press
- `transform: translateY(0)`, sombra volta ao repouso. Sem "shrink scale".

### Borders
- 1px sólido, cor `var(--border-light)` em repouso, `rgba(199,164,68,0.3-0.5)` em hover/active
- 2px em estado `Mui-focused` dos inputs (borda ouro sólido)
- **Auth card:** `1px solid rgba(199,164,68,0.18)`

### Sombras
- 5 níveis de `--elevation-1..5` (pares de Material shadows)
- **Dark theme:** sombras densas pretas (`rgba(0,0,0,0.16-0.30)`)
- **Light theme:** sombras softer (`rgba(0,0,0,0.08-0.16)`)
- `--glow-primary`: `0 0 20px rgba(199,164,68,0.25)` — exclusivo em KPI hover
- Inner shadows: usadas em `0 0 0 1px rgba(199,164,68,0.08) inset` para reforçar hover em KPI card

### Transparência e blur
- **Header:** `rgba(18,18,18,0.95)` + `backdropFilter: blur(20px)` — efeito "glass"
- **Sidebar:** mesma receita
- **Floating Filters:** `background.paper` + `backdropFilter: blur(16px)`
- **Cards (KPI/Section):** `backdrop-filter: blur(4px)` sutil
- **Overlay mobile sidebar:** `rgba(0,0,0,0.4)`

### Corner radii
- `4px` tags/badges pequenos
- `6px` Chip, Tooltip
- `8px` inputs, botões, IconButton, Alert (base `shape.borderRadius`)
- `12px` Paper, Card, Dialog, cards de seção
- `20px` Auth cards (Login/ForgotPassword) — exceção deliberada
- `16px` Profile pill no header — exceção pill completo
- `999px` scrollbar thumb — nunca em botões/chips

### Cards visuais
- BG `var(--surface-elevated)` em dark, `var(--surface)` em light
- Border `1px solid var(--border-light)`
- Radius 12-20px dependendo do contexto
- `::before` pseudo-element com linha de acento dourada 1px no topo (10%-90% gradient)
- KPI cards têm **orbe de cor** absoluto no bottom-right (140x140 circle, opacity baixa)

### Layout rules (fixed)
- Header: **altura 64px**, fixed
- Sidebar: **72px colapsada** / **260px expandida** / **280px mobile drawer**
- Sidebar expandida funciona como **overlay** sobre o conteúdo no desktop (mantém 72px de margem)
- Touch targets mínimos: **40x40px** IconButton, **44px** input height (WCAG 2.5.5)
- Content maxWidth **1920px**, centered

### Imagery vibe
- Logos são o único recurso gráfico da marca. Não há fotografia, ilustração ou ícones customizados. Tudo é Material Icons Rounded + logos SVG.
- Charts (recharts/chart.js) usam a paleta: ouro primário, depois azul `#2196F3`, verde `#4CAF50`, roxo `#7C3AED` como séries secundárias (todas vistas em KPI cards: `rgba(33,150,243,0.07)`, `rgba(76,175,80,0.07)`, `rgba(124,58,237,0.07)`).

---

## ICONOGRAPHY

### Regra de ouro: **SEMPRE `Rounded` do Material Icons**.

```jsx
// ✅ CORRETO — variante Rounded é obrigatória
import MenuRoundedIcon from '@mui/icons-material/MenuRounded'
import SearchRoundedIcon from '@mui/icons-material/SearchRounded'

// ❌ PROIBIDO
import MenuIcon from '@mui/icons-material/Menu'
import MenuOutlinedIcon from '@mui/icons-material/MenuOutlined'
import MenuSharpIcon from '@mui/icons-material/MenuSharp'
```

### Biblioteca
- **`@mui/icons-material` v7.3.5** — usada no frontend real.
- Nesta design system usamos `@mui/material` via React **ou** **Material Symbols Rounded** via Google Fonts CDN (`https://fonts.googleapis.com/icon?family=Material+Symbols+Rounded`) para artefatos estáticos HTML. A aparência é equivalente (mesmo metal model, cantos rounded).

### SVG assets (copiados em `assets/`)
- `LOGO-SEJAAP-POSITIVO.svg` / `NEGATIVO.svg` — logo principal (com gradiente dourado no positivo)
- `LOGO-SEJAAP-SLOGAN-*.svg` — variantes com tagline
- `LOGO-ELITE-POSITIVO.svg` / `NEGATIVO.svg` — marca histórica
- `favicon.ico`

### Tamanhos
| Contexto | Size prop | Container |
|---|---|---|
| Sidebar nav | `fontSize="small"` (20px) | — |
| Header IconButtons | `fontSize="small"` (20px) | **40x40** |
| KPI icon | `fontSize="inherit"` | **46x46** com `border-radius: 15px` |
| Quick access | `fontSize="small"` | 40x40 |
| Empty state | `sx={{ fontSize: 48 }}` | — |

### Emoji
- **Uso utilitário apenas**: 🏢 em empty state "selecione empresa", 🔬 em debug mode pill, ✅⚠️❌ℹ️ como marcadores em docs/guias. **Nunca como ícones primários de UI.**

### Unicode
- `·` (middle dot U+00B7) como separador em links do login.
- `—` (em dash) em loading state quando valor numérico ainda não carregou.

---

## CAVEATS (things a reader should know)

- **Sem acesso a Figma.** Toda a base vem de código + UI_GUIDE.md. Se houver um Figma oficial, variações pictóricas podem divergir.
- **Fonts carregadas via Google Fonts CDN** (Montserrat + Roboto). Se seu ambiente precisa self-hosted, baixe em `fonts/` e ajuste `@import` do `colors_and_type.css`.
- **Icon font via CDN (Material Symbols Rounded)** em artefatos HTML; o produto real usa `@mui/icons-material` em React. Parity visual validada.
- **Charts não foram recriados nesta design system** — apenas referenciados nas cards KPI coloridas. Recharts + chart.js seguem as mesmas cores semânticas definidas em `colors_and_type.css`.
