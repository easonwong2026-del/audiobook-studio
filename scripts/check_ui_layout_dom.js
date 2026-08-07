/*
 * Paste into the Gradio page DevTools console after each top-level navigation.
 * It reports the main-area bounds, global horizontal overflow, and the role
 * choices container that Gradio 5.50 renders for the Radio component.
 */
(() => {
  const main = document.querySelector(".main-area");
  const mainRect = main?.getBoundingClientRect();
  const choices = document.querySelector(
    ".role-management-list > div:has(> label), .role-management-list [role=\"radiogroup\"]",
  );
  const roleLabels = choices
    ? [...choices.children].filter((element) => element.tagName === "LABEL")
    : [];
  const result = {
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      devicePixelRatio: window.devicePixelRatio,
    },
    document: {
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      horizontalOverflow:
        document.documentElement.scrollWidth > document.documentElement.clientWidth,
    },
    mainArea: main
      ? {
          x: Math.round(mainRect.x * 100) / 100,
          width: Math.round(mainRect.width * 100) / 100,
          clientWidth: main.clientWidth,
          scrollWidth: main.scrollWidth,
          overflowX: getComputedStyle(main).overflowX,
        }
      : null,
    roleList: choices
      ? {
          count: roleLabels.length,
          flexDirection: getComputedStyle(choices).flexDirection,
          flexWrap: getComputedStyle(choices).flexWrap,
          overflowX: getComputedStyle(choices).overflowX,
          overflowY: getComputedStyle(choices).overflowY,
          clientWidth: choices.clientWidth,
          scrollWidth: choices.scrollWidth,
          clientHeight: choices.clientHeight,
          scrollHeight: choices.scrollHeight,
        }
      : null,
  };
  console.table(result.mainArea ? [result.mainArea] : []);
  console.table(result.roleList ? [result.roleList] : []);
  console.log(result);
  window.audiobookStudioLayoutCheck = result;
  return result;
})();
