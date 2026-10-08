import { Monitor, Moon, Sun } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useTheme, type ThemePreference } from '@/hooks/use-theme'
import { apiUrl } from '@/lib/api'

function ThemeSwitch() {
  const { preference, setPreference } = useTheme()
  return (
    <ToggleGroup
      type="single"
      value={preference}
      onValueChange={(value) => value && setPreference(value as ThemePreference)}
      aria-label="Colour theme"
      className="border border-border"
    >
      <ToggleGroupItem value="light" aria-label="Light theme" className="size-8">
        <Sun />
      </ToggleGroupItem>
      <ToggleGroupItem value="dark" aria-label="Dark theme" className="size-8">
        <Moon />
      </ToggleGroupItem>
      <ToggleGroupItem value="system" aria-label="Use system theme" className="size-8">
        <Monitor />
      </ToggleGroupItem>
    </ToggleGroup>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-svh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:bg-background focus:px-2">
        Skip to content
      </a>
      <header className="rule-bottom">
        <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-6 px-4 sm:px-6">
          <Link to="/" className="flex items-baseline gap-2 font-semibold tracking-tight">
            <span aria-hidden="true" className="inline-block size-3 translate-y-px bg-signal" />
            <span className="text-[17px]">Geomeasure</span>
          </Link>
          <nav aria-label="Primary" className="flex items-center gap-5 text-sm">
            <Link to="/" className="text-muted-foreground hover:text-foreground">
              Files
            </Link>
            <a href={apiUrl('/docs')} target="_blank" rel="noreferrer" className="text-muted-foreground hover:text-foreground">
              API reference
            </a>
          </nav>
          <div className="ml-auto">
            <ThemeSwitch />
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-[1600px] flex-1 px-4 pb-16 sm:px-6">
        {children}
      </main>
    </div>
  )
}
