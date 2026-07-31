# The category filter collapses behind a button on a phone, because open it costs the whole first screen

At 390×844 the fourteen chips stack about 300px tall. Measured on the built site, the first screen was header, h1, subtitle, tab row and filter bar — the first meeting's title appeared at the very bottom edge and not one agenda item was visible. The index exists to get a reader into the right meeting; a screen with no meeting on it fails that outright, on the device named as primary.

Below 640px the chips and the Clear button are replaced by one button, `Filter by topic`. The button carries the count when filters are on (`Filter by topic (2)`) and takes the accent colour, so a collapsed bar can never hide an active filter — a reader who scrolls back to a short list can see why it is short. Open, the chips take the full width under the button rather than sharing its row.

Above 640px nothing changes: the bar is one or two rows there, and collapsing it would only add a tap.

## Considered options

**Leave it open and scroll past it.** Rejected: the reader pays 300px on every visit for a control most visits never use.

**A horizontally-scrolling chip row.** Rejected. It fits the height budget but hides most of the fourteen categories off-screen with no affordance, and horizontal scroll inside a vertically-scrolling page is easy to trigger by accident. A closed control that says how many filters are on is more honest than an open one whose contents you cannot see.

**Cut the category list down to five or six.** Rejected as a separate question. Which categories exist is a content decision, not a layout one, and it would still leave a bar that costs more than it earns on the first screen.

The bar re-collapses on every load rather than remembering that it was open. The first screen is meetings, always; a reader who wants the chips is one tap from them, and the count on the button means a remembered filter is never invisible.

## Consequences

The filter is one tap away instead of zero on phones. That is the trade, and it is the right way round here: most readers scroll rather than filter, so the control taxing everyone to serve a few was the wrong default.

Discoverability is the open cost. A closed button says less about what is behind it than fourteen visible chips do. The intended repair is to preview the contents in the label — "Filter by Transit and more..." with the named category cycling every second or so — which advertises what expanding gets you without spending the height. Not built; see `TODO.md`.

The filter still only covers loaded meetings (`TODO.md` item 6). Collapsing it does not change that, and the scope note still says so when a filter is on.
