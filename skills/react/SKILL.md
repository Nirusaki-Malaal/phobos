---
name: react
description: React discovery router — detect toolchain, map routes/state/data, record run/build commands. Load when the task is understanding a React/Next.js repo (mentions, stack-discovery hit, package.json with react). Delegates authoring and review quality to vercel-react-best-practices, vercel-composition-patterns, and frontend-design.
---

# React (router)

> You own repo **discovery and mapping**. Authoring/review quality lives upstream.
> Shared Hermes glue (vault writes, `file:lines` citations, Telegram posts) is canonical
> in the `agents` skill.

## Discover (cite `package.json:lines`, configs, sampled files)
1. **Toolchain**: `react`/`next` versions, `react-router-dom`, bundler (`vite`/`webpack`/`cra`), scripts (`dev`/`build`/`start`), `vite.config.*`/`next.config.*`/`tsconfig.json`/`tailwind.config.*`.
2. **Structure**: `src/` vs Next.js `app/` (App Router) vs `pages/`; `components/`, `hooks/`, `store/`, `lib/api`; entry (`main.tsx`/`index.tsx`/`layout.tsx`).
3. **Model**: function vs class sample (3–5 files), hooks, state (`useState`/`Context`/`Zustand`/`Redux`), styling (Tailwind/CSS Modules/styled/MUI), routing, data layer (`fetch`/`axios` vs `react-query`/`swr` vs server actions), forms.
4. **Build & env**: `tsconfig` strictness + `paths`, `VITE_*`/`NEXT_PUBLIC_*` as secret refs only. Dockerfile present → also load `docker` shim; Node backend → `nodejs` shim.

## Delegate (load via `skill_view`)
| Need | Load |
|---|---|
| Writing/reviewing components, perf, data-fetching, bundle | `vercel-react-best-practices` (70 rules + `rules/*.md`) |
| Refactoring boolean-prop sprawl, component architecture | `vercel-composition-patterns` |
| Visual/UX direction, anti-generic-AI aesthetics | `frontend-design` |
