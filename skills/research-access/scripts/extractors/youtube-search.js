(options) => {
  const WATCH_URL = "https://www.youtube.com/watch?v=";
  const SNIPPET_LENGTH = 160;
  const joinRuns = (runs) => (runs ?? []).map((run) => run.text).join("");
  const toVideo = (video) => ({
    title: joinRuns(video.title?.runs),
    channel: joinRuns(video.longBylineText?.runs ?? video.ownerText?.runs),
    views: video.viewCountText?.simpleText ?? video.shortViewCountText?.simpleText ?? "",
    published: video.publishedTimeText?.simpleText ?? "",
    length: video.lengthText?.simpleText ?? "",
    snippet: joinRuns(video.detailedMetadataSnippets?.[0]?.snippetText?.runs).slice(0, SNIPPET_LENGTH),
    url: WATCH_URL + video.videoId,
  });
  const primary = window.ytInitialData?.contents?.twoColumnSearchResultsRenderer?.primaryContents;
  const sections = primary?.sectionListRenderer?.contents ?? [];
  const items = sections.flatMap((section) => section.itemSectionRenderer?.contents ?? []);
  const videos = items.map((item) => item.videoRenderer).filter(Boolean);

  return { results: videos.slice(0, options.limit).map(toVideo) };
}
