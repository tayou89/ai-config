---
name: research-access
description: Which research tool reaches which kind of site, and one-call scripts that list results or read one source on search engines, video, review stores, community sites, and the Korean portal. Apply before reading those sites, or when a search or fetch is refused or returns nothing.
user-invocable: false
---

# Research Access

Source choice and the "search wide, read narrow" method are in `product-planning`. This skill covers how to reach the sources cheaply. Access was checked on 2026-11-06 on one machine; when a result contradicts a note, re-check it and update the date.

## Which tool reaches what

- The built-in web search and fetch tools use a crawler that some sites refuse: Reddit and Naver Blog return an error, and searches limited to Naver Cafe or Tistory return no links. A refusal or an empty result does not mean no sources exist.
- The built-in browser pane refuses Reddit and Naver for safety reasons. ego-browser, which uses a normal browser profile, reaches them, and the scripts below run in it.
- Ordinary articles and documentation pages are cheapest through the fetch tool, which returns a summary; use the scripts for result lists and for sites the fetch tool cannot read.
- Requesting a YouTube caption file directly returns an empty body, and the App Store review feed returns no entries; the scripts read the page instead.

## One-call scripts

Run `python3 -I ~/.claude/skills/research-access/scripts/research.py <command> <target>`. Each call is one browser round trip and prints compact JSON; keep `--limit` (default 5) and `--max-chars` (default 6000) as small as the question allows.

| Command | Target | Returns |
| --- | --- | --- |
| `google` | search words | titles, links, and the source line (site, comment count, age) |
| `youtube` | search words | title, channel, views, age, length, link |
| `transcript` | video URL | channel, views, date, length, and the transcript with a time mark about every 30 seconds |
| `naver` | search words | blog and cafe posts with title, summary, disclosure words (sponsored or self-paid), link |
| `reddit` | thread URL | title, score, comment count, date, body, top comments with scores |
| `play` | package name | rating, review count, downloads, recent reviews with date, rating, and helpful count |
| `appstore` | numeric app id | rating, rating count, recent reviews with date, rating, and title |
| `page` | any URL | the main text of one page, for sites the fetch tool cannot read |

Pick from a list before reading: run the list command, choose two or three items by relevance, credibility, popularity, and date, then run `transcript`, `reddit`, or `page` on those only. Find an App Store id with `curl -s "https://itunes.apple.com/search?term=<name>&country=kr&entity=software&limit=3"`.

Exit code 2 means the site asks for a human check: stop and tell the user; never solve it. The scripts print ego-browser update notices to stderr; mention an update once and upgrade only with the user's approval.

## Issue trackers and release notes

Use the GitHub CLI and request only the fields needed:

- `gh search issues "<words>" --repo <owner>/<repo> --sort reactions --limit 5 --json title,url,commentsCount,updatedAt,state`
- `gh release list -R <owner>/<repo> -L 5`, then `gh release view <tag> -R <owner>/<repo> --json name,publishedAt,body`

## Public data

Find datasets with the web search limited to the portal's domain. Downloading a file needs the user's approval. Then print only the needed part with `python3 -I ~/.claude/skills/research-access/scripts/table-peek.py <file.csv> --columns <A,B> --where <COLUMN=TEXT> --limit 10`, which reads both UTF-8 and the legacy Korean encoding.

## Limits

Read only: do not sign in, post, vote, or follow. Read a few pages for the question at hand, not bulk collections. Leave reviewer and commenter names out of reports.
