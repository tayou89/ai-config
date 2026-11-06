async (options) => {
  const SEGMENT_SELECTOR = "transcript-segment-view-model, ytd-transcript-segment-renderer";
  const BUTTON_SELECTOR = "ytd-video-description-transcript-section-renderer button, button[aria-label]";
  const SHOW_TRANSCRIPT_PATTERN = /스크립트 표시|show transcript/i;
  const TRANSCRIPT_PATTERN = /스크립트|transcript/i;
  const TIME_PATTERN = /^(\d{1,2}:)?\d{1,2}:\d{2}/;
  const SPOKEN_TIME_PATTERN = /^(\d+\s*(시간|분|초|hours?|minutes?|seconds?),?\s*)+/i;
  const MARK_INTERVAL_SECONDS = 30;
  const POLL_COUNT = 10;
  const POLL_INTERVAL_MS = 750;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const labelOf = (button) => (button.innerText || "") + " " + (button.getAttribute("aria-label") || "");
  const toSeconds = (time) => time.split(":").reduce((total, part) => total * 60 + Number(part), 0);
  const findTranscriptButton = () => {
    const buttons = [...document.querySelectorAll(BUTTON_SELECTOR)];

    return (
      buttons.find((button) => SHOW_TRANSCRIPT_PATTERN.test(labelOf(button))) ??
      buttons.find((button) => TRANSCRIPT_PATTERN.test(labelOf(button)))
    );
  };
  const waitForSegments = async () => {
    let segments = [];

    for (let attempt = 0; attempt < POLL_COUNT && segments.length === 0; attempt += 1) {
      await sleep(POLL_INTERVAL_MS);
      segments = [...document.querySelectorAll(SEGMENT_SELECTOR)];
    }
    return segments;
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
  await sleep(POLL_INTERVAL_MS);
  findTranscriptButton()?.click();
  const segments = await waitForSegments();

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
