async (options) => {
  const SEGMENT_SELECTOR = "transcript-segment-view-model, ytd-transcript-segment-renderer";
  const PANEL_SELECTOR = "ytd-engagement-panel-section-list-renderer";
  const VISIBILITY_ATTRIBUTE = "visibility";
  const EXPANDED_VISIBILITY = "ENGAGEMENT_PANEL_VISIBILITY_EXPANDED";
  const BUTTON_SELECTOR = "ytd-video-description-transcript-section-renderer button, button[aria-label]";
  const SHOW_TRANSCRIPT_PATTERN = /스크립트 표시|show transcript/i;
  const TRANSCRIPT_PATTERN = /스크립트|transcript/i;
  const TIME_PATTERN = /^(\d{1,2}:)?\d{1,2}:\d{2}/;
  const SPOKEN_TIME_PATTERN = /^(\d+\s*(시간|분|초|hours?|minutes?|seconds?),?\s*)+/i;
  const MARK_INTERVAL_SECONDS = 30;
  // ego-browser stops page.evaluate after about 15 seconds, so both waits together stay under that.
  const BUTTON_TIMEOUT_MS = 6000;
  const SEGMENT_TIMEOUT_MS = 6000;
  const POLL_INTERVAL_MS = 250;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const labelOf = (button) => (button.innerText || "") + " " + (button.getAttribute("aria-label") || "");
  const toSeconds = (time) => time.split(":").reduce((total, part) => total * 60 + Number(part), 0);
  const findButton = (pattern) => {
    const buttons = [...document.querySelectorAll(BUTTON_SELECTOR)];

    return buttons.find((button) => pattern.test(labelOf(button)));
  };
  const findTranscriptButton = () => findButton(SHOW_TRANSCRIPT_PATTERN) ?? findButton(TRANSCRIPT_PATTERN);
  const hasShowTranscriptButton = () => Boolean(findButton(SHOW_TRANSCRIPT_PATTERN));
  const isExpandedPanel = (panel) => panel?.getAttribute(VISIBILITY_ATTRIBUTE) === EXPANDED_VISIBILITY;
  const findSegments = () => {
    // YouTube can keep a hidden copy of the transcript panel; reading the whole page returns every line twice.
    const segments = [...document.querySelectorAll(SEGMENT_SELECTOR)];
    const panels = [...new Set(segments.map((segment) => segment.closest(PANEL_SELECTOR)))];
    const panel = panels.find(isExpandedPanel) ?? panels[0];

    return segments.filter((segment) => segment.closest(PANEL_SELECTOR) === panel);
  };
  const hasSegments = () => findSegments().length > 0;
  const waitUntil = async (isReady, timeoutMs) => {
    const deadline = Date.now() + timeoutMs;

    while (!isReady() && Date.now() < deadline) {
      await sleep(POLL_INTERVAL_MS);
    }
  };
  const toText = (segments) => {
    let nextMark = 0;
    let text = "";

    for (const segment of segments) {
      const raw = segment.innerText.replace(/\s+/g, " ").trim();
      const time = (raw.match(TIME_PATTERN) ?? [""])[0];
      const words = raw.slice(time.length).trim().replace(SPOKEN_TIME_PATTERN, "");

      if (time && toSeconds(time) >= nextMark) {
        text += `\n[${time}] ${words}`;
        nextMark = toSeconds(time) + MARK_INTERVAL_SECONDS;
      } else {
        text += " " + words;
      }
    }
    return text.trim();
  };
  const details = window.ytInitialPlayerResponse?.videoDetails ?? {};
  const microformat = window.ytInitialPlayerResponse?.microformat?.playerMicroformatRenderer ?? {};
  const header = {
    title: details.title ?? document.title.replace(/ - YouTube$/, ""),
    channel: details.author ?? "",
    views: details.viewCount ?? "",
    published: microformat.publishDate ?? "",
    lengthSeconds: details.lengthSeconds ?? "",
  };

  document.querySelector("#description-inline-expander #expand")?.click();
  await waitUntil(hasShowTranscriptButton, BUTTON_TIMEOUT_MS);
  findTranscriptButton()?.click();
  await waitUntil(hasSegments, SEGMENT_TIMEOUT_MS);
  const segments = findSegments();

  if (segments.length > 0) {
    const text = toText(segments);

    return {
      ...header,
      segments: segments.length,
      totalChars: text.length,
      truncated: text.length > options.maxChars,
      transcript: text.slice(0, options.maxChars),
    };
  } else {
    return { ...header, transcript: null, reason: "No transcript panel or captions on this video." };
  }
}
