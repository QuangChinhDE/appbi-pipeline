'use client';

import * as React from 'react';

import { type Locale, translate } from '@/lib/i18n';

interface I18nValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
  /**
   * Translate the first key the catalog actually knows.
   * Server-supplied enums grow faster than the catalog, so an unmapped value
   * degrades to the next candidate (usually the raw value) instead of showing
   * a bare key like "run.SOME_NEW_STATE" to the user.
   */
  tf: (keys: string[], fallback: string, vars?: Record<string, string | number>) => string;
}

const I18nContext = React.createContext<I18nValue | null>(null);
const STORAGE_KEY = 'appbi.integration.locale';

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = React.useState<Locale>('vi');

  React.useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === 'vi' || stored === 'en') setLocaleState(stored);
  }, []);

  const setLocale = React.useCallback((next: Locale) => {
    setLocaleState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
    document.documentElement.lang = next;
  }, []);

  const value = React.useMemo<I18nValue>(
    () => ({
      locale,
      setLocale,
      t: (key, vars) => translate(locale, key, vars),
      tf: (keys, fallback, vars) => {
        for (const key of keys) {
          const translated = translate(locale, key, vars);
          if (translated !== key) return translated;
        }
        return fallback;
      },
    }),
    [locale, setLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const context = React.useContext(I18nContext);
  if (!context) {
    // Rendering outside the provider (e.g. an error boundary) should degrade,
    // not crash the page.
    return {
      locale: 'vi',
      setLocale: () => {},
      t: (key, vars) => translate('vi', key, vars),
      tf: (keys, fallback, vars) => {
        for (const key of keys) {
          const translated = translate('vi', key, vars);
          if (translated !== key) return translated;
        }
        return fallback;
      },
    };
  }
  return context;
}
