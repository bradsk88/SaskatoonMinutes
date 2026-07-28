# TODO — index and details pages

Priority order. Top item first. Written 2026-07-26 from a review of
`app/templates/index.html` and `app/templates/meeting.html`.

## Decided (do not re-litigate)

- **The index skims, the details page proves.** Density belongs on the
  details page. The card exists to make a reader open the right meeting.
- **Mobile-first.** Anything reachable only by hover is missing. A hover
  tooltip is not a place to keep a fact.
- **No meeting-level summary.** Considered and dropped: the top topics'
  Descriptions already are the summary, and a second written layer would
  be a paraphrase of a paraphrase.
- **The filter must eventually search the whole archive.** Filtering only
  what is on screen tells a resident "no housing items" when there are
  some. Same family of failure as a summarize run that ships empty.

---

## Done

Items 1–5 are implemented; the numbering below is kept so the remaining
items keep the priority they were given.

- **1. Escaping** — `item.title`, `item.section_number` and `data.error`
  now go through `escapeHtml`; `escapeAttr` on the index covers `>` and
  null. Pinned by `tests/test_page_identity_contract.py`.
  The open section below is the original description and is stale — it
  names two line numbers that were fixed long ago. Re-audited on
  2026-07-28 while building the feeds: every title path was already
  escaped, and the one gap left was the meeting id going into an `href`
  unencoded. Now `encodeURIComponent` at the source and `escapeAttr` at
  the seam.
- **2. Meeting identity** — `MeetingDetail` gained `title`/`date`/
  `start_time`, read from the agenda HTML that `load_detail` already
  fetches (`_extract_meeting_info`), so it costs no extra request. The
  header shows the body and the date; the tab title matches. An
  unidentifiable meeting reads "Meeting", never "City Council".
- **3. Duplicate summary** — the extractive one-liner no longer renders.
  `summarize_agenda_items` still writes the key for API callers.
- **4. Card redesign** — shape C. Five topics, each a plain sentence,
  outcome badge only. "N other items" comes from `count_agenda_items`
  over the agenda, so it is a real count. The topics payload is now
  `{topics, total_items}`; a bare list still loads. The filter says it
  covers loaded meetings only.

- **5. Hover-only content** — closed out by 4 plus one change: what
  "Not discussed" means is now a line of text at the top of the agenda,
  said once rather than repeated on every item in a consent block. The
  two `data-tooltip` uses left on the detail page are hints on buttons
  that already show a timestamp ("Jump to this item in the video"), not
  content — losing them on touch costs a reader nothing.

### Found by running the build against live eSCRIBE

The full build (310 meetings, 16 bodies) surfaced four things the
synthetic checks could not, all now fixed:

- **The two pages disagreed about meeting size.** The card said 43
  items, the header said 73 — the header counted rendered rows, which
  include recesses and section headers. Both now read one `item_count`
  produced by `count_agenda_items`.
- **Consent rows linked to the wrong audio.** The card offered a play
  link and a `?t=` deep link built from an inherited timestamp, which
  identifies the parent section's recording. Both suppressed, matching
  what the detail page already did.
- **Council meetings had no start time.** A council agenda page's
  `<time>` carries a date and no clock; a committee page carries both.
  The meetings list has it for all of them, so the build falls back to
  `_start_time_24h` over eSCRIBE's formatted string.
- **Body names were truncated and inconsistently cased.** The agenda
  heading is hard-wrapped with `<br/>`, so keeping the last line alone
  produced "On Transportation" (20 pages), "Advisory Committee" (50) and
  "And Corporate Services" (19). All lines but the document-kind line are
  now kept. The meetings-list fallback is titleized too, so names no
  longer shout on some pages and not others.

Also fixed from the rendered result: at 390px a long title was squeezed
into five words a line with the outcome pinned beside it. The two now
stack under the existing 640px breakpoint.

### A second mobile pass over the card — `0013`–`0016`

The card was still a page rather than a card: measured over the 276
built cards at 390px, the average drew **36 text lines** and the worst
54, where a phone screen holds about 40. Bounding the row (`0013`),
labelling the fallback instead of apologising for it (`0014`) and
dropping the duplicate play link (`0015`) take the mean to **20 and the
worst case to a hard 25**. Outcome badges stopped shouting (`0016`).

`_site/` was re-rendered from its own embedded payload to check the
result on real content; nothing has been deployed.

---

## 1. Escape upstream text before rendering it

`app/templates/meeting.html:413` puts `item.title` straight into
`innerHTML`. `:61` does the same with `data.error`. Every other field on
the page goes through `escapeHtml`. The titles come from eSCRIBE, so
this is upstream HTML landing in the page unfiltered.

Small, isolated, and there is no reason to carry it another day.

## 2. Say which meeting the details page is showing

`app/templates/meeting.html:109` hardcodes `City Council Meeting` — wrong
on the police, governance and public-hearing tabs — and the page shows
**no date at all**. The tab title is `Meeting Details - YXEMinutes`.

- Header: date, weekday, start time, and the body that met.
- `{% block title %}`: same, so bookmarks and shared links are readable.

A page you cannot identify after landing on it fails before any of the
content questions matter.

## 3. Delete the extractive one-liner from item rendering

`app/templates/meeting.html:421` renders `extractive_summary` outside
both the Summary and Notes views, so it sits permanently beside the
written Description. Two summaries of the same item, the worse one
unlabelled.

The Description replaced it. Remove the render; check whether anything
still needs `extractive_summary` in the API response.

## 4. Rebuild the index card — shape C

The card becomes 3–5 topics, each one plain sentence:

```
Wed, June 24, 2026 · 9:30 AM
City Council

Property tax, 2027           [Approved]
The 2027 mill rate rises 4.2%, about
$9 a month on an average home.

Sutherland infill rezoning   [Approved]
Four lots on Central Ave can now hold
up to eight units each.

Snow route parking bans      [Deferred]
Sent back to staff for costing.

13 other items                [Open →]
```

- The line under each topic is its written Description. Raw agenda text
  keeps its existing marker (`index.html:247`) rather than being passed
  off as one.
- **Outcome badge only.** Category and chip badge rows leave the card
  (`index.html:200–219`). Keep `data-categories` on the row — the filter
  reads it, and it is independent of what is drawn.
- Trailing "N other items" replaces the bare Open button.

Do this before the mobile work below: it removes most of the hover-only
content on its own.

**In the same pass:** relabel the filter bar to say it filters loaded
meetings, until item 6 lands. A filter that quietly lies is worse than
one that admits its range.

## 5. Give every hover tooltip a tap equivalent — done, see above

`data-tooltip` carries real content that touch devices cannot reach:

- Outcome detail on index rows (`index.html:182`).
- Two-letter category codes — `ZD`, `PT`, `AT` (`index.html:62`). These
  are unreadable even with a tooltip; a first-time reader has no way in.
- The consent-item explanation and the older-summary note.

Tap-to-expand, or inline the text. Item 4 shrinks this list; finish what
it leaves.

## 6. Filter across all meetings, not the loaded ones

`applyFilters` (`index.html:510`) hides rows in cards already in the DOM.
With infinite scroll that means the archive is invisible to the filter.

Needs an API that filters by category server-side, and category data per
meeting available without loading every topic list. Separate job from
item 4 — the card redesign does not depend on it, and this one is
backend work.

## 7. Link to a single agenda item — done

Built 2026-07-28, because the feed needed it: an entry has to point at
one item, and `?t=<ms>` could not address a consent item at all.

`#item-<item_id>` on every agenda card. The id is set as the card is
drawn, and the page scrolls to it after rendering — a fragment the
browser resolves against a skeleton is silently dropped, which is what
made this look impossible. Reuses the reveal a timestamp link already
gets: centred, because the sticky player owns the top of the viewport,
and flashed, so a page that jumps says where it went. Recess rows share
`item_id` -1 and are excluded.

The index card links to the item too, in the same pass. A row's href now
carries both parts, which answer different questions:

- `?t=<ms>` — seek the video to this moment.
- `#item-<id>` — put the reader on this item.

A consent item gets the anchor and no seek, so for the first time the
rows most likely to be missed can be pointed at. The detail page skips
the seek's scroll when a hash is present, or the row was revealed twice.

Verified by running the built page under jsdom rather than by eye: 36
items drawn, 36 with ids, no `item--1` from a recess, and one instant
scroll plus its correction on arrival against one smooth scroll for a
`?t=` click. A browser screenshot could not settle it — the capture
lands before the scroll.

## 8. Let the details page carry the density

Once the card is thin, the details page is the only place chips,
categories, votes and attachments live. Check nothing was only ever
visible on the index.

## 9. Re-measure the sticky player on resize

`setupStickyVideo` (`meeting.html:508`) measures pixel sizes once. Rotate
a phone and the spacer height and scale are stale. Matters more now that
mobile is the target reader.

## 10. Navigating a long agenda

60-item meetings get a scroll and a back-to-top button. In-page jump or
filter. Lowest priority — real, but everything above changes what the
page is before this changes how you move through it.

---

## Decided, waiting on your ratification

Eleven ADRs, all reversible, nothing committed. See `docs/adr/0006`–`0016`.

- `0006` — the filter bar collapses behind a button below 640px.
- `0007` — the phone keeps the h1 and drops the tagline, the hero
  subtitle and the "Recent Meetings" heading.
- `0008` — Section Headers and recesses cannot take a topic slot
  (they were 23% of them).
- `0009` — an item with a written Description outranks one without.
- `0010` — card titles are titleized and lose trailing file numbers.
- `0011` — a consent row leads with its outcome, then "in consent, not
  debated". 103 approvals and one defeat were being reported as
  "Not discussed".

- `0012` — a row earns a card slot by having a recorded outcome, floor
  of three. Rows with no outcome fell from a third of slots to 0.7%.
  (Your call, from the interview: outcome as the bar, "received as
  information" counts, pad to three.)

- `0013` — a card row is two lines of title and three of summary.
  Cards averaged 36 lines on a screen that holds about 40; 35% of
  titles and 75% of summaries are now clipped for someone.
- `0014` — "From the agenda:" replaces "Older summary — no
  plain-language description available", a two-line caveat that was on
  71% of rows.
- `0015` — the ▶ button leaves the row. Its href was the row's own href
  on all 871 rows that had one.
- `0016` — a card's outcome badge is sentence case. Over half of rows
  carry a label of 22 characters or more, and `.badge` shouted them.

## Decisions waiting on you

- Nothing open on the index card. Items 6–10 above are untouched.
- Smaller, not taken: the phone still spends ~270px above the first
  card — the sticky header, `main`'s 2rem top padding, and an h1 that
  wraps to two lines. `0007` kept the h1 deliberately. Trimming the
  padding is worth ~20px and no information; say the word.

## 11. Advertise what the collapsed filter holds

The mobile filter button reads "Filter by topic", which says a filter
exists and nothing about what it filters by. Cycle a real category
through the label — "Filter by **Transit** and more..." — changing every
second or so, so the contents are visible without the height.

From ratifying `0006`: collapsed is right, obscured is the cost, and
this is the way to pay it down.

## 12. Publish the index as an Atom feed — done

Built 2026-07-28. Two files, written by `app/feeds.py` on every build:

- `/feed.xml` — one entry per calendar day the city sat, bodies as
  headings inside it. The default.
- `/feed-items.xml` — one entry per qualifying item.

The gate held: an item earns an entry with a Description or an
interpretive chip. What changed is the ranking. Measured against three
real meetings, the card's ranking misses the longest debates of the
year, so the feed ranks on `discussion_minutes` directly and caps at 8
per meeting — ADR `0018`, and item 15 below.

A day publishes once settled: every meeting on it has summaries, or
seven days have passed. ADR `0019` has the reasoning, including why the
feed keeps no record of what it published.

Left undone on purpose:

- **No per-body feeds.** Sixteen bodies, fifteen of which would be empty
  for months at a time. The body rides along as an Atom `<category>`, so
  a reader that filters can.
- **Retention is 30 days and 100 items**, not the whole archive.
- **The feed is not linked from the header**, only the footer and
  autodiscovery.

## 13. Cap or repair the broken discussion spans

22 item spans run over three hours and four run about 6.9 days — "Blake
Tait – Denounce 1 Million March 4 Children" clocks 9,876 minutes. Broken
end bookmarks. Any duration ranking inherits it, and the detail page's
timeline probably shows it too.

## 14. A defeated item should say why on the index

Brad's note, 2026-07-27. "Approved" needs no explanation; **defeated**
does. A resident scanning the index wants the reason council said no, and
that is the row most likely to be the reason they came.

Small and worth checking before designing: **15 of ~1,900 live rows are
defeated** — `Defeated` ×12, plus `Defeated (5-6)`, `(0-9)`, `(3-7)`.

Most of the "why" is already extracted; the card just does not
prioritize it:

- *1st Avenue BRT (Link) Concept Changes*, defeated 5–6, carries Debate
  Highlight, Who's Affected and Staff vs. Council chips.
- *Priority Based Budgeting Criteria*, defeated 0–9, has Staff vs.
  Council — "Council rejected the administration's proposed criteria".
- *Municipal Tax Policy*, defeated 3–7, has **no chips at all** and a raw
  agenda description that opens "Councillor Jeffries introduced the item
  as chair…". For this one the why does not exist yet, so no amount of
  card work surfaces it.

So it is probably two changes, not one:

- The card picks its single takeaway from `TAKEAWAY_ORDER`
  (`index.html:193`), which is the same for every row. A defeated row
  could prefer the categories that explain a no — Dissenting View, Staff
  vs. Council, Debate Highlight — over Who's Affected or Cost.
- Where no such chip exists, this is the coverage ceiling again, not a
  rendering bug. See the note above about 956 rows with no chips.

Not designed. Ask Brad what a defeated row should read like before
building it — a mock row in `AskUserQuestion` is what has worked.

## 15. The card ranking saturates at twenty minutes and misses the biggest debates

Found 2026-07-28 while designing the feed (item 12), by running
`extract_meeting_topics` against three real meetings and comparing it to
a plain duration ranking over the same substance-gated items.

`summarizer.py:96`:

```
duration_score = 0.25 * min(1.0, _discussion_minutes(item) / 20.0)
```

Above twenty minutes every item scores the same, so the longest debates
are separated only by dollar signs in the title and dot-count in the
section number. What that costs, top 8 per meeting:

- **Nov 25 budget** — misses Capital Options (155.5m), Housing and
  Homelessness funding (83.6m) and Arts, Culture and Events Venues
  (81.3m). Includes Land Development (3.5m) and Community Support
  (10.2m).
- **Dec 3** — misses Downtown Event and Entertainment District (100.3m)
  and Development Incentives Policy (71.1m). Includes a right-of-way
  dedication (1.4m) and two items with no recorded discussion.
- **Dec 17** — 6 of 8 agree. Misses an 11.0m item.

The saturation is defensible on a card where every row is one tap from
the full page, which is the job it was written for. It is worth
re-checking now that duration data is better than it was.

Two candidate fixes, neither designed: raise or remove the cap, or make
the score log-scaled so a two-hour debate outranks a twenty-minute one
without swamping the other signals. Item 13 first — the dirty spans feed
this.

Related: the feed does **not** reuse this function, for exactly this
reason. See item 12.

## 16. One try/catch owns the whole detail page render

Found 2026-07-28 while verifying the item anchors under jsdom.

`loadMeeting` (`meeting.html:46`) wraps everything from parsing the
payload to drawing the last card in a single `try`. Its `catch` replaces
the header with "Failed to load meeting details." and leaves the agenda
reading "Loading agenda..." forever.

So anything that throws after the data is in hand takes the whole page
with it. The one that surfaced was `setupStickyVideo` — a missing
`IntersectionObserver` erased a fully-loaded 73-item agenda. That
particular case is an artifact of the test environment and cannot happen
in a browser that supports the site, which is why this is latent rather
than a bug report.

What is real is the shape: the video player, the sticky behaviour and the
sort toggle are decorations around the agenda, and a decoration failing
should cost its own feature, not the page. The message is also wrong for
this class of failure — the details did load.

Narrow the `try` to fetching and parsing, and let the presentational
setup fail on its own. **Lowest priority of the numbered items**: nothing
in production reaches it today.

## Noted, not scheduled

- Tabs reorder themselves by recency on every load
  (`sortTabsByRecency`, `index.html:117`), so the default tab and the tab
  positions change week to week.
- Tab and filter state stay out of the URL. Item-level sharing was judged
  the part that matters; this is the rest of it.
- `CHIP_GROUP` (`meeting.html:141`) is a hand-copied mirror of
  `CATEGORY_GROUP` in `app/item_categorizer.py`, and
  `outcomeExplanation` (`meeting.html:198`) restates the outcome
  vocabulary from `CONTEXT.md` in string matching. Both drift silently.
- `calcBatchSize` (`index.html:128`) assumes a 136px card. Real cards are
  several times that, so the first batch overshoots the viewport.
- No free-text search anywhere.
