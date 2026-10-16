/**
 * Panel translations.
 *
 * ## Why this is a module and not a pile of inline strings
 *
 * The panel had no translation layer at all: every visible word was a literal in
 * the TSX. That makes "change the language" impossible without editing every
 * page, and it lets the two languages drift apart invisibly -- a string added to
 * one page is English forever, with nothing to notice.
 *
 * The dictionary is typed, so adding a key to one language and forgetting the
 * other is a compile error rather than an English word in a Persian page.
 * `lang-check.ts` checks the same property at runtime against the shipped
 * bundle, because a type error cannot catch an empty string or a placeholder
 * that exists in only one language.
 *
 * ## The shape of a key
 *
 * Dotted, namespaced by surface: `nav.dashboard`, `kpi.targets`, `sys.database`.
 * Flat rather than nested, so a parity check is a set comparison and a missing
 * key names itself in the error.
 *
 * ## Placeholders
 *
 * `{name}` style. The parity check compares placeholder *sets* between the two
 * languages, because a `{time}` present in one and absent in the other either
 * throws at render time or silently drops the substitution.
 *
 * ## Coverage is partial, and deliberately visible
 *
 * This dictionary currently covers the application shell -- navigation, brand,
 * topbar, breadcrumbs, common actions, page titles, the dashboard's tiles and
 * panels, and the system-status block. Page bodies further down (table headers
 * on some pages, filter labels, chart legends on pages other than the dashboard)
 * are still English literals in their TSX. That is a known gap rather than a
 * finished translation, and `page.sweep` plus the parity check are what will
 * find the remainder.
 */

export type Lang = 'en' | 'fa'

export const LANGUAGES: ReadonlyArray<{ value: Lang; label: string; dir: 'ltr' | 'rtl' }> = [
  { value: 'en', label: 'English', dir: 'ltr' },
  { value: 'fa', label: 'فارسی', dir: 'rtl' },
]

export const DEFAULT_LANG: Lang = 'en'

const en = {
  'app.name': 'Argus',
  'app.subtitle': 'admin panel',

  'nav.dashboard': 'Dashboard',
  'nav.telegramUsers': 'Telegram Users',
  'nav.targets': 'Targets',
  'nav.audit': 'Audit Log',
  'nav.rules': 'Rules',
  'nav.settings': 'Settings',
  'nav.styleGuide': 'Style Guide',

  'chrome.search': 'Search',
  'chrome.openCommandPalette': 'Open command palette',
  'chrome.refresh': 'Refresh',
  'chrome.switchTheme': 'Switch theme',
  'chrome.accountMenu': 'Account menu',
  'chrome.signOut': 'Sign out',
  'chrome.collapseSidebar': 'Collapse sidebar',
  'chrome.expandSidebar': 'Expand sidebar',
  'chrome.openNavigation': 'Open navigation',
  'chrome.language': 'Language',
  'chrome.changeLanguage': 'Change language',
  'chrome.superadmin': 'superadmin',

  'action.addTarget': 'Add target',
  'action.viewAudit': 'View audit',
  'action.viewAll': 'View all',
  'action.cancel': 'Cancel',
  'action.save': 'Save',
  'action.delete': 'Delete',
  'action.confirm': 'Confirm',
  'action.close': 'Close',
  'action.retry': 'Try again',
  'action.copy': 'Copy',

  'page.dashboard.title': 'Dashboard',
  'page.targets.title': 'Targets',
  'page.targets.empty': 'No targets yet',
  'page.rules.title': 'Rules',
  'page.rules.empty': 'No rules configured yet',
  'page.audit.title': 'Audit Log',
  'page.audit.empty': 'No audit entries yet',
  'page.settings.title': 'Settings',
  'page.users.title': 'Telegram Users',
  'page.users.empty': 'No Telegram users yet',
  'page.styleGuide.title': 'Style Guide',
  'page.targetDetail.title': 'Target',
  'page.login.title': 'Sign in',

  'kpi.targets': 'Targets',
  'kpi.targets.hint': 'registered',
  'kpi.scansToday': 'Scans today',
  'kpi.changesToday': 'Changes today',
  'kpi.changesToday.hint': 'Difference events recorded today across all targets and profiles.',
  'kpi.activeScans': 'Active scans',
  'kpi.failed24h': 'Failed (24h)',
  'kpi.failed24h.hint': 'scans',
  'kpi.vsYesterday': 'vs yesterday',
  'kpi.workerHint': '{running} running · {queued} queued · {slots} slot(s)',
  'kpi.schedule': 'Schedule',
  'kpi.schedule.on': 'on',
  'kpi.schedule.off': 'Off',
  'kpi.schedule.disabledInSettings': 'disabled in settings',

  'chart.scanActivity': 'Scan activity — last {days} days',
  'chart.scanActivity.sub': 'scans and change events per day',
  'chart.changeTypes': 'Change types — last {days} days',
  'chart.series.scans': 'scans',
  'chart.series.failed': 'failed',
  'chart.series.changes': 'changes',

  'panel.recentActivity': 'Recent activity',
  'panel.viewAll': 'View all',
  'panel.mostChanged': 'Most-changed targets',
  'panel.lastDays': 'last {days}d',
  'panel.systemStatus': 'System status',

  'sys.database': 'Database',
  'sys.reachable': 'reachable',
  'sys.down': 'down',
  'sys.hostsRecorded': 'Hosts recorded',
  'sys.servicesRecorded': 'Services recorded',
  'sys.lastScan': 'Last scan',
  'sys.telegramUsers': 'Telegram users',

  'common.loading': 'Loading…',
  'common.none': 'None',
  'common.yes': 'Yes',
  'common.no': 'No',
  'common.enabled': 'Enabled',
  'common.disabled': 'Disabled',
  'common.name': 'Name',
  'common.value': 'Value',
  'common.actions': 'Actions',
  'common.search': 'Search',
  'common.export': 'Export',
  'common.error': 'Something went wrong',
  'common.updatedAgo': 'updated {time} ago',

  'empty.noActivity': 'No activity yet',
  'empty.noChanges': 'No change events',
  'empty.nothingLogged': 'Nothing logged yet',
} as const

const fa: Record<keyof typeof en, string> = {
  'app.name': 'آرگوس',
  'app.subtitle': 'پنل مدیریت',

  'nav.dashboard': 'داشبورد',
  'nav.telegramUsers': 'کاربران تلگرام',
  'nav.targets': 'اهداف',
  'nav.audit': 'تاریخچه',
  'nav.rules': 'قوانین',
  'nav.settings': 'تنظیمات',
  'nav.styleGuide': 'راهنمای سبک',

  'chrome.search': 'جستجو',
  'chrome.openCommandPalette': 'باز کردن جعبه فرمان',
  'chrome.refresh': 'تازه‌سازی',
  'chrome.switchTheme': 'تغییر پوسته',
  'chrome.accountMenu': 'منوی حساب',
  'chrome.signOut': 'خروج',
  'chrome.collapseSidebar': 'جمع کردن نوار کناری',
  'chrome.expandSidebar': 'باز کردن نوار کناری',
  'chrome.openNavigation': 'باز کردن ناوبری',
  'chrome.language': 'زبان',
  'chrome.changeLanguage': 'تغییر زبان',
  'chrome.superadmin': 'مدیر ارشد',

  'action.addTarget': 'افزودن هدف',
  'action.viewAudit': 'مشاهده تاریخچه',
  'action.viewAll': 'مشاهده همه',
  'action.cancel': 'انصراف',
  'action.save': 'ذخیره',
  'action.delete': 'حذف',
  'action.confirm': 'تأیید',
  'action.close': 'بستن',
  'action.retry': 'تلاش دوباره',
  'action.copy': 'کپی',

  'page.dashboard.title': 'داشبورد',
  'page.targets.title': 'اهداف',
  'page.targets.empty': 'هنوز هدفی ثبت نشده است',
  'page.rules.title': 'قوانین',
  'page.rules.empty': 'هنوز قانونی تنظیم نشده است',
  'page.audit.title': 'تاریخچه',
  'page.audit.empty': 'هنوز ردیفی ثبت نشده است',
  'page.settings.title': 'تنظیمات',
  'page.users.title': 'کاربران تلگرام',
  'page.users.empty': 'هنوز کاربر تلگرامی ثبت نشده است',
  'page.styleGuide.title': 'راهنمای سبک',
  'page.targetDetail.title': 'هدف',
  'page.login.title': 'ورود',

  'kpi.targets': 'اهداف',
  'kpi.targets.hint': 'ثبت‌شده',
  'kpi.scansToday': 'اسکن‌های امروز',
  'kpi.changesToday': 'تغییرات امروز',
  'kpi.changesToday.hint': 'رویدادهای تغییر ثبت‌شده امروز در همه اهداف و پروفایل‌ها.',
  'kpi.activeScans': 'اسکن‌های فعال',
  'kpi.failed24h': 'ناموفق (۲۴ ساعت)',
  'kpi.failed24h.hint': 'اسکن',
  'kpi.vsYesterday': 'در مقایسه با دیروز',
  'kpi.workerHint': '{running} در حال اجرا · {queued} در صف · {slots} جایگاه',
  'kpi.schedule': 'زمان‌بندی',
  'kpi.schedule.on': 'روشن',
  'kpi.schedule.off': 'خاموش',
  'kpi.schedule.disabledInSettings': 'در تنظیمات غیرفعال شده',

  'chart.scanActivity': 'فعالیت اسکن — {days} روز اخیر',
  'chart.scanActivity.sub': 'اسکن و رویداد تغییر در روز',
  'chart.changeTypes': 'انواع تغییر — {days} روز اخیر',
  'chart.series.scans': 'اسکن‌ها',
  'chart.series.failed': 'ناموفق',
  'chart.series.changes': 'تغییرات',

  'panel.recentActivity': 'فعالیت اخیر',
  'panel.viewAll': 'مشاهده همه',
  'panel.mostChanged': 'اهداف با بیشترین تغییر',
  'panel.lastDays': '{days} روز اخیر',
  'panel.systemStatus': 'وضعیت سیستم',

  'sys.database': 'پایگاه‌داده',
  'sys.reachable': 'در دسترس',
  'sys.down': 'قطع',
  'sys.hostsRecorded': 'میزبان‌های ثبت‌شده',
  'sys.servicesRecorded': 'سرویس‌های ثبت‌شده',
  'sys.lastScan': 'آخرین اسکن',
  'sys.telegramUsers': 'کاربران تلگرام',

  'common.loading': 'در حال بارگذاری…',
  'common.none': 'هیچ',
  'common.yes': 'بله',
  'common.no': 'خیر',
  'common.enabled': 'فعال',
  'common.disabled': 'غیرفعال',
  'common.name': 'نام',
  'common.value': 'مقدار',
  'common.actions': 'عملیات',
  'common.search': 'جستجو',
  'common.export': 'خروجی',
  'common.error': 'مشکلی پیش آمد',
  'common.updatedAgo': '{time} پیش به‌روزرسانی شد',

  'empty.noActivity': 'هنوز فعالیتی نیست',
  'empty.noChanges': 'رویداد تغییری نیست',
  'empty.nothingLogged': 'هنوز چیزی ثبت نشده است',
}

export type MessageKey = keyof typeof en

export const MESSAGES: Record<Lang, Record<MessageKey, string>> = { en, fa }

/** Every key, for parity checks. */
export function keysOf(lang: Lang): string[] {
  return Object.keys(MESSAGES[lang]).sort()
}

/** Placeholder names used by a message, e.g. ['time'] for "updated {time} ago". */
export function placeholdersOf(message: string): string[] {
  const found = message.match(/\{[a-zA-Z0-9_]+\}/g)
  return found ? found.map((f) => f.slice(1, -1)).sort() : []
}

/**
 * Look up a message, substituting `{placeholder}` values.
 *
 * A key missing from the requested language falls back to English rather than
 * rendering the key. The parity check exists precisely so a missing key is
 * caught before it matters; this fallback is for a deployed bundle, not for
 * development.
 *
 * A placeholder with no matching value is left in place rather than replaced
 * with "undefined" -- a visible `{time}` says the substitution was missed, while
 * "undefined" reads like data.
 */
export function t(key: MessageKey, lang: Lang, values?: Record<string, string | number>): string {
  const message = MESSAGES[lang]?.[key] ?? MESSAGES[DEFAULT_LANG][key] ?? key
  if (!values) return message
  return message.replace(/\{([a-zA-Z0-9_]+)\}/g, (whole, name: string) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole,
  )
}

export function dirOf(lang: Lang): 'ltr' | 'rtl' {
  return LANGUAGES.find((l) => l.value === lang)?.dir ?? 'ltr'
}