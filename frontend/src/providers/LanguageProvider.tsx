'use client';

import * as React from 'react';

import { authApi } from '@/lib/api';

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

/**
 * English until somebody chooses otherwise.
 *
 * This was Vietnamese, which made every deployment Vietnamese by default and
 * every screen a mix: the chrome followed the setting while anything the
 * server produced -- connector field labels, error messages -- stayed in one
 * language regardless. English is the safer default for a product that ships
 * to people whose language we do not know.
 */
const DEFAULT_LOCALE: Locale = 'en';

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = React.useState<Locale>(DEFAULT_LOCALE);
  //: A choice, once made, belongs to the reader. The deployment default
  //: arrives a moment later over the network and must not overwrite it.
  const chosen = React.useRef(false);

  React.useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === 'vi' || stored === 'en') {
      chosen.current = true;
      setLocaleState(stored);
    }
  }, []);

  // What this deployment speaks, from its own `.env`. Asked rather than
  // compiled into the bundle, so shipping to a Vietnamese team is a setting
  // rather than a rebuild. The endpoint is public because the sign-in page is
  // the first thing rendered and has to be in the right language too.
  React.useEffect(() => {
    let cancelled = false;
    authApi.config()
      .then((config) => {
        if (cancelled || chosen.current) return;
        if (config.default_locale === 'vi' || config.default_locale === 'en') {
          setLocaleState(config.default_locale);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const setLocale = React.useCallback((next: Locale) => {
    chosen.current = true;
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
      locale: DEFAULT_LOCALE,
      setLocale: () => {},
      t: (key, vars) => translate(DEFAULT_LOCALE, key, vars),
      tf: (keys, fallback, vars) => {
        for (const key of keys) {
          const translated = translate(DEFAULT_LOCALE, key, vars);
          if (translated !== key) return translated;
        }
        return fallback;
      },
    };
  }
  return context;
}
