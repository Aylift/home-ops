import { createI18n } from 'vue-i18n'
import pl from './locales/pl.js'
import en from './locales/en.js'

// Polish is the default; English is the alternative. The choice is persisted to
// localStorage so it survives reloads.
const STORAGE_KEY = 'lang'
const SUPPORTED = ['pl', 'en']

function initialLocale() {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved && SUPPORTED.includes(saved)) return saved
  return 'pl'
}

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: initialLocale(),
  fallbackLocale: 'en',
  messages: { pl, en },
})

export function setLocale(locale) {
  if (!SUPPORTED.includes(locale)) return
  i18n.global.locale.value = locale
  localStorage.setItem(STORAGE_KEY, locale)
  document.documentElement.lang = locale
}

export const SUPPORTED_LOCALES = SUPPORTED
