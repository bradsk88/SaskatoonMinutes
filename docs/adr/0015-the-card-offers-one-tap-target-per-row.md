# A card row offers one tap target, because the play button pointed at the row's own link

Each card row drew a purple ▶ button in the right margin, on **871 of 1,255 rows (69%)** — every row with a timestamp that is not a consent item. Its href was:

```
${openHref}?t=${t.time_start_ms}
```

The row around it was already a link to:

```
openHref + '?t=' + t.time_start_ms
```

The same string, under the same condition. Two tap targets, one destination. The button was removed; the row link is unchanged.

Looking at it on a phone is what surfaced it. The ▶ sits vertically centred against the summary, so it lines up with no part of the row in particular, and it takes 1.75rem plus a gap out of the summary column — on a 390px card that is enough to add a wrap to most rows that carry one.

## Considered options

**Keep it and make it seek the video without leaving the index.** Rejected. There is no player on the index, and putting one there makes the card a viewer — the opposite of a page you read to decide what to open.

**Keep it as an affordance: it signals that this item has video.** Rejected. It marked 69% of rows, so as a signal it was close to constant, and the thing it distinguishes — the 31% without video, mostly consent items — is already stated on those rows by "in consent, not debated".

**Move it inline with the title instead of removing it.** Rejected. It is still the same link twice, now closer together.

## Consequences

The video is one tap further away for nobody: the same tap on the same row does the same thing. What is lost is the visual cue that the row leads to video — the whole card is a set of links now, uniformly, and nothing on it says which of them lands on a timestamp.

The `.topic-play-link` rules, including their dark-mode overrides, were deleted with it.
