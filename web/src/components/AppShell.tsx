import { AudioLines, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

export function AppShell({
  children,
  label,
  actions,
  icon: Icon = AudioLines,
  skipLabel = 'Skip to voice call',
}: {
  children: ReactNode;
  /** Shown after the brand, such as the signed-in customer or "Staff desk". */
  label: string;
  actions?: ReactNode;
  icon?: LucideIcon;
  skipLabel?: string;
}) {
  return (
    <>
      <a
        href="#main-content"
        className="fixed top-3 left-3 z-10 -translate-y-20 rounded-lg bg-stone-900 px-4 py-3 text-sm font-medium text-white focus-visible:translate-y-0"
      >
        {skipLabel}
      </a>
      <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 sm:py-10">
        <header className="flex items-center justify-between gap-4 rounded-t-xl border border-stone-200 bg-white p-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <span
              className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-orange-50 text-orange-700"
              aria-hidden="true"
            >
              <Icon className="size-5" strokeWidth={1.75} />
            </span>
            <span className="shrink-0 text-sm font-semibold whitespace-nowrap">Acme Corp</span>
            <span className="shrink-0 text-stone-300" aria-hidden="true">
              /
            </span>
            <span className="truncate text-sm text-stone-600">{label}</span>
          </div>
          {actions}
        </header>
        <main
          id="main-content"
          tabIndex={-1}
          className="overflow-hidden rounded-b-xl border-x border-b border-stone-200 bg-white shadow-sm"
        >
          {children}
        </main>
      </div>
    </>
  );
}
