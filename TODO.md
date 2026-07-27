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

## 7. Link to a single agenda item

Deep links are `?t=<ms>` only (`meeting.html:641`). Consent items have no
distinct timestamp, so **the items most likely to be missed are the ones
that cannot be pointed at**. Sharing one item is the sharing that matters
here; a shareable filtered index is not.

Stable per-item anchors that do not depend on video position.

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

## 12. Publish the index as an RSS feed, one entry per major discussion

The index is going out as a feed. A feed entry has no tabs, no filter
and no hover — it stands alone or it fails.

**Selected**: an item earns an entry by having something to say — a
written Description or an interpretive chip. **Sorted** by how long
council spent on it.

The line that carries the chip is built and on the card already — see
`0017`. What is left is the feed itself: the XML, the entry granularity
and the per-item links it needs (item 7).

Measured over the archive to settle the gate:

- 1,612 of 6,952 items carry their own time span (23%). Consent items
  inherit their parent's; 22 meetings have no timings at all.
- 1,025 items ran 5 minutes or longer, 1–5 per meeting for most
  meetings. Median discussed item is 8.4 minutes.
- But only 266 of those have a Description and 326 an interpretive
  chip. Duration alone would publish ~700 entries reading "council
  spent twenty minutes on this" with no account of what came of it.

So substance gates and duration ranks. ~300 entries today, growing with
summarize coverage.

Depends on nothing else, but see 13 — the durations are dirty.

## 13. Cap or repair the broken discussion spans

22 item spans run over three hours and four run about 6.9 days — "Blake
Tait – Denounce 1 Million March 4 Children" clocks 9,876 minutes. Broken
end bookmarks. Any duration ranking inherits it, and the detail page's
timeline probably shows it too.

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
