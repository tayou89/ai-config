(options) => {
  const HUMAN_CHECK_PATTERN = /unusual traffic|sorry\/index|recaptcha/i;
  const SUMMARY_LENGTH = 220;
  const collapse = (text) => text.replace(/\s+/g, " ").trim();
  const toItem = (heading) => {
    const anchor = heading.closest("a");
    const box = heading.closest("[data-hveid], .MjjYud, .g") ?? heading.parentElement;
    const title = collapse(heading.innerText);

    return {
      title,
      url: anchor ? anchor.href : "",
      summary: collapse(box.innerText).replace(title, "").trim().slice(0, SUMMARY_LENGTH),
    };
  };

  if (HUMAN_CHECK_PATTERN.test(location.href + " " + document.title)) {
    return { humanCheck: true };
  } else {
    const items = [...document.querySelectorAll("#rso h3, #search h3")].map(toItem).filter((item) => item.url);

    return { results: items.slice(0, options.limit) };
  }
}
