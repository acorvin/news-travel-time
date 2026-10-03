# The Speed of News

How long the news of eleven events took to reach American newspapers, and later news websites, in four eras between 1799 and 2023.

**[View the poster](https://acorvin.github.io/news-travel-time/)**

Each event is drawn as a plume of lines rising from the moment it happened. Every line is one newspaper or one news website, and its height is the time the news took to reach it, on a scale from one minute to four months. Hover over a line to see the paper or site, the place and the passage that carried the news.

The study compares four eras. It does not measure the years between them, and the page shows them as gaps.

| Era | Events | Source |
|---|---|---|
| Horse and sail | 1799, 1814 | American newspapers |
| The telegraph arrives | 1845, 1850, 1865 | American newspapers |
| Wireless, radio and television | 1912, 1941, 1963 | American newspapers |
| The web | 2019, 2022, 2023 | News websites worldwide |

<!--SUMMARY-->
| Event | Year | Sources | Half had it within |
|---|---|---|---|
| Death of George Washington | 1799 | 8 newspapers | 18 days |
| Treaty of Ghent signed | 1814 | 10 newspapers | 55 days |
| Death of Andrew Jackson | 1845 | 85 newspapers, 11 daily | 9 days (dailies) |
| Death of Zachary Taylor | 1850 | 95 newspapers, 12 daily | 2 days (dailies) |
| Assassination of Abraham Lincoln | 1865 | 151 newspapers, 30 daily | 3 days (dailies) |
| Sinking of the Titanic | 1912 | 612 newspapers, 103 daily | 28 hours (dailies) |
| Attack on Pearl Harbor | 1941 | 105 newspapers, 16 daily | 35 hours (dailies) |
| Assassination of John F. Kennedy | 1963 | 55 newspapers, 8 daily | 14 hours (dailies) |
| Notre-Dame de Paris fire | 2019 | 6,620 news websites, 1,816 with a page time | 4 hours (page times) |
| Death of Elizabeth II | 2022 | 4,563 news websites, 1,610 with a page time | 4 hours (page times) |
| Turkey–Syria earthquake | 2023 | 3,758 news websites, 1,593 with a page time | 7 hours (page times) |
<!--/SUMMARY-->

## Method

**Newspapers.** For eight events from 1799 to 1963, the Library of Congress's [Chronicling America](https://www.loc.gov/collections/chronicling-america/) collection was searched for the words a report would use, from the day of the event until a window had closed. Each paper's pages were then read in date order through the Library's OCR text, and the paper is placed at the first page whose text reports the event. A regular expression per event, listed in `data/reference/events.json`, decides what counts as a report.

A paper carries a date but not a time, so its arrival is a window: from the start of the printed date (or the event itself, if that came later) to midnight at the end of it, in local solar time. Summary figures use the end of the window, so every newspaper time is an upper bound and every speed a lower bound. Distances run from the event to the paper's town, located with the US Census gazetteer, and fall back to the county or state centre when the town cannot be matched.

**Daily and weekly papers.** Most papers in the collection were weeklies, and a weekly printed the news on its next issue day, however fast the news had come. From 1845 on, the half-way figures count only daily papers. Every paper is still drawn, with the weeklies fainter. For 1799 and 1814 there are almost no dailies, so all papers count. Kennedy has only eight daily papers in the collection, so its figure rests on a small number.

**News websites.** For three events from 2019 to 2023, [GDELT](https://www.gdeltproject.org/) supplies the list of articles. It reads news sites around the world and publishes what it found every 15 minutes, with a machine translation of the names, places and themes in articles that are not in English. `scripts/collect_web.py` reads those files for the 48 hours after the news became public and keeps the articles whose address, title, names, places and themes meet the event's rules in `events.json`, with words for the event in some forty languages. For Elizabeth II, words of her death must appear in the address or the title, so articles about her health before the announcement do not count, and articles about what would happen if she died are skipped.

GDELT's time is when its crawler found an article, which for many sites is hours after it was published: for the sites where both times are known, the median gap is close to two hours. `scripts/web_published.py` therefore opens each site's first article and reads the publication time in the page's own metadata, taking only the main one, the first time given by the most specific tag the page has (a publication meta tag such as `article:published_time`, then the page's structured data, then a `<time>` tag). That time is used when it carries a time zone and falls between the moment the news was public and GDELT's time. Between a quarter and two-fifths of the sites, depending on the event, give such a time; the rest have moved, refuse the request or give none, and keep GDELT's later time. Because those later times would make the web look slower than it was, the half-way figures for the web count only the sites with a page time, and the others are drawn fainter. Each plume draws 600 sites, chosen at random. The clock for every event starts at the event itself, so Elizabeth II's plume includes the 3 hours and 20 minutes before her death was announced.

The hover card shows each article's headline: GDELT's copy of the title where it has one (it has none for 2019), otherwise the title on the page, otherwise the words in the article's address.

## Checks that changed the data

- **Treaty of Ghent.** The first pass counted six reports from December and January. They were a false rumour from Philadelphia on 8 January and mentions of other treaties. The treaty reached America on HMS Favorite, which anchored off Sandy Hook on the evening of 11 February 1815, so no earlier report can be genuine, and the search starts on that date. One more paper had matched a treaty with Indian tribes reported the same week, and that match is now skipped.
- **Andrew Jackson.** Rumours of Jackson's death ran in Eastern papers days before he died, and were denied. In the same week the Montreal papers announced the death of Sir Richard Jackson. A match is skipped when words such as "without foundation", "not yet" or "Sir Richard" sit within 300 characters of it, and reading moves on to the next match.
- **Pearl Harbor on the day.** Three papers dated the morning of 7 December were first counted because they mention Pearl Harbor: a marine home on leave, a housing project and two recruits bound for the base. They were printed before the attack. The rule for the first day now asks for words of an attack, and the three papers are placed at their first reports on 8 December.
- **Lincoln on the day.** Four papers dated 15 April 1865 matched the search without reporting the shooting: Honolulu wrote about the Russian emperor, Portland recalled the 1861 plot, Towson used "assassin" as a figure of speech, and Sioux City printed a comic sketch. They are listed in `data/reference/newspaper_exclusions.csv` and left out, because their first real report was not collected.
- **Trusted searches.** For Lincoln and the Titanic, and for Pearl Harbor and Kennedy after the first day, the search terms only appear once the event has happened, so the Library's own text match confirms the paper. One page is still read for a quotation, and a paper whose quotation cannot be matched is kept and flagged `search`.

## Limitations

- The eras are samples. The years between them are not measured, and the newspapers and the websites are different kinds of source, so the comparison is between eras, not a continuous line.
- Chronicling America holds only the papers that libraries have digitized, weighted toward small-town weeklies and uneven by state and decade. It ends in 1963, and its 1960s holdings are thin.
- OCR misses words on faded or damaged pages, so some papers printed the news earlier than shown. The first report found is never earlier than the true one.
- The Titanic, Pearl Harbor and Kennedy searches use front pages only. A paper that ran the story inside first is placed at its first front-page report, which can only make the news look slower.
- Washington and Taylor were read with an earlier limit of 10 pages per paper, and six of their papers returned no match.
- One results page of the "attack hawaii" search failed twice and was not collected. Most of its papers are also found by the "pearl harbor" search.
- The web collection stops 48 hours after the news became public, so a site that first reported later is not counted, and the half-way figures for the web era are slightly fast.
- GDELT covers the sites it reads, not the whole web. The web figures rest on the sites whose pages give a publication time, between a quarter and two-fifths of them, on the assumption that these sites were no faster or slower than the rest. A site whose first report used words the rules do not catch is placed at a later article.
- The rules for the web catch some articles about other stories that mention the event, such as a fire at the Al-Aqsa mosque the same evening. A reading of the headlines of the Notre-Dame sites with a page time puts these at about one in a hundred, and since they are spread across the 48 hours, they barely move the half-way figure.
- Website lines are not placed by distance, because a site's location says little about where its readers are.

## Reproduce

```
pip install -r requirements.txt
python scripts/collect.py          # Census gazetteer
python scripts/collect_web.py      # news websites, from GDELT
python scripts/web_published.py    # publication times printed on the articles
python scripts/build.py            # tables in data/processed/, page in docs/
```

The Library of Congress search sits behind a bot check that scripts cannot pass, so the newspaper collection runs in a browser tab. Instructions are at the top of `scripts/browser/collect-newspapers.js`. The browser downloads a JSON file, and `python scripts/import_newspapers.py` turns it into the tables in `data/raw/newspapers/`. The published tables layer later passes over the first one, in the order of the files in `data/raw/`: the full collection, a second pass for Ghent and Jackson, a correction for three Pearl Harbor papers, and Kennedy. Given several files, the import script lets each later file replace the events it holds.

## Files

| Path | What it holds |
|---|---|
| `data/reference/events.json` | The eras and events: times, places, search terms and the rules for a report |
| `data/raw/newspapers/` | One row per paper: the first page that reported the event, with a quotation |
| `data/raw/web_sources.csv` | One row per news website: when GDELT first found its report, and the publication time and headline on the article's own page |
| `data/processed/arrivals.csv` | Every arrival with its distance and time |
| `data/processed/events_summary.csv` | The figures per event |
| `docs/` | The page, published with GitHub Pages; `standalone.html` has the data inlined |
| `embed/squarespace-snippet.html` | An iframe for embedding the page on another site |

## Sources

- Library of Congress, *Chronicling America: Historic American Newspapers*. Public domain.
- The GDELT Project, Global Knowledge Graph 2.1.
- US Census Bureau, 2024 Gazetteer Files.

© 2026 Alex Corvin. All rights reserved. The page, design and code may not be reused without permission. The newspaper pages come from the Library of Congress and are in the public domain; the website records come from the GDELT Project, under its own terms.
