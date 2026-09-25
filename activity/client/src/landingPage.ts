/**
 * Tracker #0135. Shown whenever the Activity was started by Discord's own Launch button (App
 * Directory profile, app launcher) instead of one of the bot's buttons: no guild resolvable in a
 * DM, or bridge screen 'landing' inside a server. Tells a new user what ClashControl is and how to
 * start — add it to a server, or the first commands to run once it's there.
 *
 * Strings come from the `activity.landing` namespace (GET /api/i18n). FALLBACK_STRINGS only
 * covers keys that fetch didn't return — all of them when it fails (bot offline / bridge down).
 * This page needs nothing else from the bot, so it should still be readable then.
 */
import type { Translator } from './i18n'

export const FALLBACK_STRINGS: Record<string, string> = {
  tagline: "Your clan's command center for Clash of Clans",
  intro:
    'ClashControl tracks your clans around the clock and keeps your Discord server in sync with the game: roles, wars, CWL rosters, reminders and stats.',
  feature_clan: 'Clan management with automatic Discord roles',
  feature_cwl: 'CWL management, including multi-clan setups',
  feature_predictions: 'Live war predictions',
  feature_notifications: 'Reminders for open attacks and raid weekends',
  feature_stats: 'Leaderboards and detailed player and clan stats',
  feature_languages: 'English, German, Spanish, Chinese and Latin. Missing your language? Request it with /feature and we will gladly provide it.',
  install_title: 'New here? Add ClashControl to your server',
  install_text: 'You need the Manage Server permission on the server you add it to.',
  install_button: 'Add to server',
  members_title: 'Already on your server? Get started',
  members_text: 'Type these commands in your server, or in a DM with ClashControl:',
  members_registration: 'link your Clash of Clans accounts and set up war reminders',
  members_cwl: 'tell your clan leaders whether you want to play CWL',
  members_help: 'see everything ClashControl can do',
  admins_title: 'Server admins: set up your clan',
  admins_subscribe: 'start tracking your clan and post a leaderboard in this channel',
  admins_management: 'language, war notification channel, auto roles and clan families',
  support_title: 'Need help?',
  support_text: 'Ask your questions in our public support channel on The QCrew server.',
  support_button: 'Open support channel',
  footer: 'Found a bug or have an idea? Use /bug or /feature.',
}

const FEATURES: [string, string][] = [
  ['🛡️', 'feature_clan'],
  ['🏆', 'feature_cwl'],
  ['🔮', 'feature_predictions'],
  ['🔔', 'feature_notifications'],
  ['📊', 'feature_stats'],
  ['🌍', 'feature_languages'],
]
// Slash command names are not localized on Discord's side, so they stay literal here.
const MEMBER_STEPS: [string, string][] = [
  ['/registration', 'members_registration'],
  ['/cwl preferences', 'members_cwl'],
  ['/help', 'members_help'],
]
const ADMIN_STEPS: [string, string][] = [
  ['/subscribe clan:#TAG', 'admins_subscribe'],
  ['/clan management', 'admins_management'],
]

// ClashControl's public support channel on The QCrew server.
const SUPPORT_URL = 'https://discord.com/channels/1145641080621109312/1553052010222198865'

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

function buildSteps(steps: [string, string][], t: Translator): HTMLUListElement {
  const list = el('ul', 'landing-steps')
  for (const [command, key] of steps) {
    const item = el('li')
    item.append(el('code', undefined, command), ` — ${t(key)}`)
    list.appendChild(item)
  }
  return list
}

function buildLinkButton(label: string, url: string, className: string, openLink: (url: string) => void): HTMLButtonElement {
  const button = el('button', className, label)
  button.type = 'button'
  button.addEventListener('click', () => openLink(url))
  return button
}

/** `openLink` opens a URL outside the Activity iframe (discordSdk.commands.openExternalLink). */
export function renderLandingPage(
  root: HTMLElement,
  t: Translator,
  installUrl: string,
  openLink: (url: string) => void,
): void {
  root.textContent = ''
  const page = el('div', 'landing')

  const header = el('div', 'landing-header')
  const icon = el('img', 'landing-icon')
  icon.src = '/clashcontrol-icon.png'
  icon.alt = ''
  const titles = el('div')
  titles.append(el('h1', 'landing-title', 'ClashControl'), el('div', 'landing-tagline', t('tagline')))
  header.append(icon, titles)
  page.append(header, el('p', 'landing-intro', t('intro')))

  const features = el('ul', 'landing-features')
  for (const [emoji, key] of FEATURES) {
    const item = el('li')
    item.append(el('span', 'landing-feature-icon', emoji), el('span', undefined, t(key)))
    features.appendChild(item)
  }
  page.appendChild(features)

  const install = el('section', 'landing-card landing-card-primary')
  install.append(
    el('h2', undefined, t('install_title')),
    el('p', undefined, t('install_text')),
    buildLinkButton(t('install_button'), installUrl, 'landing-button', openLink),
  )

  const members = el('section', 'landing-card')
  members.append(el('h2', undefined, t('members_title')), el('p', undefined, t('members_text')), buildSteps(MEMBER_STEPS, t))

  const admins = el('section', 'landing-card')
  admins.append(el('h2', undefined, t('admins_title')), buildSteps(ADMIN_STEPS, t))

  const support = el('section', 'landing-card')
  support.append(
    el('h2', undefined, t('support_title')),
    el('p', undefined, t('support_text')),
    buildLinkButton(t('support_button'), SUPPORT_URL, 'landing-button landing-button-secondary', openLink),
  )

  const cards = el('div', 'landing-cards')
  cards.append(install, members, admins, support)
  page.append(cards, el('p', 'landing-footer', t('footer')))
  root.appendChild(page)
}
