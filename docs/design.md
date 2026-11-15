# Design notes

How TouchFrame looks and why, for anyone changing it. Last updated: 2026-11-15.

## The system in one paragraph

One blue accent drives everything structural: headings, the active tab, links between pages, focus. Neutrals are tinted slightly toward that blue. A second accent, a deep green, means one thing only: *happening now*. Status has its own colours (teal, amber, red, purple) and is never shown by colour alone. Headings and numbers are in Rubik, everything else in Karla. Corners are 8px. Dark is the default, because the screen is on a wall at night; light uses the same tokens.

## Tokens

`touchframe/web/tokens.css` is the brand token file, **v1.1.0**, copied in unchanged. Do not edit it here: when the tokens change, replace the file and update the version in this note.

`app.css` adds a few tokens of its own, each built from the brand ones and declared again for dark mode:

| Token | Light | Dark | Use |
|---|---|---|---|
| `--tap` | `3rem` | same | Smallest pressable thing (see Touch) |
| `--sun` | `#D1A017` | `#E3B341` | The sun in weather icons. A fill, never text |
| `--hot`, `--cold` | `--status-error`, `--accent` | same tokens | High and low temperatures |
| `--now` | `--accent-2` | same token | What is playing; the event that is on |
| `--on-status` | `#FFFFFF` | `#12161B` | Text on a status-colour fill (badge, Stop) |
| `--cat-orange` | `#A24E0C` | `#F0A572` | The one category colour with no brand token |
| `--alarm-a`, `--alarm-b` | `#C23730`, `#9F2B25` | same | The two reds an emergency page flashes between |

## Colour rules

1. **Blue leads.** Anything that needs "the brand colour" uses `var(--accent)`. No hex values in new rules.
2. **Green is scoped.** `--accent-2` has two homes: `--now` (the playing pill, a playing button, an event in progress), and as one of the colours a calendar or an intercom button can be given. It is never structure.
3. **Status is teal, amber, red.** OK is `--status-ok`, not green. Every status also has an icon or a word: a tick with "All up", a warning sign with "offline".
4. **Separate by hue.** Zabbix severities are grey, blue, amber, purple and red; Disaster is High's red, filled in, and both carry their name.
5. **Text colours are text stops; fills are fill colours.** In light mode `--status-warn` is for text and `--status-warn-border` for bars and edges.
6. **Tints come from the scale:** 8% wash, 12 to 14% resting surface, 22% pressed or hairline, 45% borders and rings only.
7. **A weather alert banner** is the same wash and edge as a button, in amber, or red when Severe. An Extreme one is filled red, with `--on-status` text.
8. **Colours chosen elsewhere** (a calendar's, an intercom button's) map onto the status hues through the `--cat-*` tokens. A button is a 14% wash of its colour over the card, with a solid edge, so its name stays in the normal text colour.

## Measured contrast

WCAG AA is 4.5:1 for text. These are the lowest pairings in each theme, measured from the token values; everything else is higher.

| Pairing | Light | Dark |
|---|---|---|
| Amber text on the page | 4.55 | 9.33 |
| Purple text on the page | 4.59 | 7.17 |
| Red text on the page | 4.66 | 7.23 |
| Muted text on the page | 4.75 | 6.79 |
| Orange text on the page | 4.99 | 8.93 |
| Muted text on a card | 5.51 | 5.21 |
| Accent on the active tab (12% tint over a card) | 6.25 | 4.93 |
| Text on a Stop button or badge | 5.41 | 7.23 |
| White on an emergency page | 5.41, 7.38 | same |

Chips sit on the card colour, not on a tint of their own hue: a tinted chip measured 3.8:1 in light mode and was dropped. Day numbers outside the month use `--tertiary` and are not meant to be read.

## Type

- Rubik 500 for the clock, temperatures, counts, headings and button names. Nothing heavier.
- Karla 400 for text, 600 for emphasis.
- Section headings are sentence case, in the accent, not capitals.
- Tabular digits wherever numbers line up or change in place.
- The smallest text is 0.8rem (12.8px on the 7" display).
- The fonts cover Latin only. Other scripts in a calendar title fall back to the system font.

## Touch

- **Size.** `--tap` is 3rem: 48px on the 800x480 display, about 9 mm. Tabs, steppers, switches, dialog buttons and the month arrows are at least that. Segmented controls are 2.6rem tall inside a 3rem track.
- **No hover, no tooltips.** Pressed states use `:active`. Nothing is explained only in a `title`.
- **Swipes follow the finger.** A sideways drag moves the page with it and settles to the nearer side; a flick counts even when short. Past the first or last page it gives a little and springs back.
- **One direction per surface.** Pages move sideways and lists scroll up and down. The only sideways scrollers are the hourly and daily strips; a swipe that starts on them scrolls them. On the month, a swipe turns the month. An alert in the Alerts list swipes left to archive (or restore) it, with the word for what it will do showing behind; to the right it only gives a little. A slow drag that stops short springs back; a flick counts only if the finger was still moving when it lifted.
- **A touch that arrives as a mouse still scrolls.** Raspberry Pi OS's desktop hands the touchscreen to Chromium as a mouse by default, and a browser doesn't scroll for a dragged mouse. `app.js` does it for mouse pointers: drag a list or a strip and it follows, let go while moving and it coasts. Real touches are left to the browser.
- **Show there is more.** Lists fade at the bottom edge. The strips show half of the next column.
- **A swipe is never a press.** The click that would follow a drag is swallowed. The touch that ends the screensaver does not reach what is underneath.
- **Ask before anything loud or disruptive:** emergency announcements, restarting, opening the Pi to the network.

## Source files

ASCII only. Special characters are written as escapes (`\u00b0`, `\u00b7`) in JavaScript and Python, and no em dashes in prose.
