"use strict";

const $ = id => document.getElementById("rd-" + id);
const book = JSON.parse($("data").textContent);
const body = $("book");
const scroll = $("scroll");
const themeChoices = [...$("theme").querySelectorAll('input[type="radio"]')];
const palette = {yellow: "黄", green: "绿", blue: "蓝", pink: "粉", purple: "紫"};
const kinds = {highlight: "划线", note: "笔记", bookmark: "书签"};
const chapterInfo = new Map(book.chapters.map(chapter => [chapter.id, chapter]));
const paragraphs = Array.from(body.querySelectorAll("[data-pid]"));
const chapterElements = new Map(book.chapters.map(chapter => [chapter.id, document.getElementById(chapter.id)]));
const chapterSequence = [...chapterElements.values()];
const byId = new Map(paragraphs.map((element, order) => {
  let text;
  return [element.dataset.pid, {element, order, get text() { return text ??= element.textContent; }}];
}));
const byChapter = new Map(book.chapters.map(chapter => [chapter.id, []]));
for (const block of byId.values()) byChapter.get(block.element.closest(".rd-chapter").id).push(block);

const key = "epub-annotation-reader:" + book.bookId;
const baseline = new Map(book.annotations.map(note => [note.id, note]));
const defaults = {theme: "paper", font: "serif", size: 20, leading: 1.85, width: 640, layout: "split", showAI: true};
let cacheError = false;
let saved = {};
try {
  saved = JSON.parse(localStorage.getItem(key) || "{}") || {};
} catch {
  cacheError = true;
}
const settings = {...defaults, ...saved.settings};
const changes = new Map(Object.entries(saved.changes || {}));
let dirty = !!saved.dirty;
let reading = saved.reading || null;
let pending = null;
let editing = null;
let returnPosition = null;
let current = null;
let toastTimer;
let flashTimer;
let storageTimer;
let paintQueued = false;
let locations = new Map();
let visible = [];
let listed = new Map();
let ticks = new Map();
let marginCards = [];
let bookmarkBlocks = new Set();
let imagesReady = false;
let userNavigated = false;

function attrButton(label, handler, className = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function notice(message) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3200);
}

function backupStatus() {
  $("backup").dataset.dirty = String(dirty);
  $("backup").textContent = cacheError
    ? "浏览器暂存不可用 · 请导出 JSON 备份"
    : dirty ? "有未备份的改动 · 请导出 JSON" : "JSON 可用于恢复笔记和阅读进度";
  $("notes-toggle").textContent = dirty ? "批注 •" : "批注";
}

function persist() {
  try {
    localStorage.setItem(key, JSON.stringify({changes: Object.fromEntries(changes), settings, dirty, reading}));
    cacheError = false;
  } catch {
    cacheError = true;
  }
  backupStatus();
}

function allNotes() {
  const notes = new Map(baseline);
  for (const [id, record] of changes) {
    if (!record || !record.note) notes.delete(id);
    else notes.set(id, record.note);
  }
  return [...notes.values()];
}

function currentPosition() {
  return {y: scroll.scrollTop, fraction: scroll.scrollTop / Math.max(1, scroll.scrollHeight - scroll.clientHeight),
    chapter: scrollChapter().id, paragraphId: activeBlock()?.dataset.pid ?? null};
}

function scrollChapter() {
  const at = scroll.getBoundingClientRect().top + 92;
  const chapters = chapterSequence;
  let low = 0, high = chapters.length - 1;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (chapters[mid].getBoundingClientRect().top <= at) low = mid;
    else high = mid - 1;
  }
  return chapters[low];
}

function activeBlock(chapter = scrollChapter()) {
  const local = byChapter.get(chapter.id);
  if (!local?.length) return null;
  const at = scroll.getBoundingClientRect().top + 100;
  let low = 0, high = local.length - 1;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (local[mid].element.getBoundingClientRect().bottom < at) low = mid + 1;
    else high = mid;
  }
  return local[low].element;
}

function positionSnapshot() {
  const position = currentPosition();
  const target = byId.get(position.paragraphId)?.element || document.getElementById(position.chapter);
  position.offset = scroll.getBoundingClientRect().top - target.getBoundingClientRect().top;
  return position;
}

function restorePosition(position) {
  if (!position || !Number.isFinite(position.y)) return;
  if (position.y < 5) {
    scroll.scrollTop = 0;
    return;
  }
  const target = byId.get(position.paragraphId)?.element || document.getElementById(position.chapter);
  scroll.scrollTop = target
    ? absoluteTop(target) + (Number.isFinite(position.offset) ? position.offset : -34)
    : Number.isFinite(position.fraction) ? position.fraction * Math.max(0, scroll.scrollHeight - scroll.clientHeight) : position.y;
}

function rememberReturn() {
  returnPosition ||= positionSnapshot();
  $("return").hidden = false;
}

function absoluteTop(element) {
  return element.getBoundingClientRect().top - scroll.getBoundingClientRect().top + scroll.scrollTop;
}

function jump(element, flash = true, range = null) {
  if (!element) return;
  userNavigated = true;
  const quoteTop = range?.getClientRects()[0]?.top ?? element.getBoundingClientRect().top;
  scroll.scrollTop = Math.max(0, quoteTop - scroll.getBoundingClientRect().top + scroll.scrollTop - 39);
  if (flash) {
    body.querySelector(".rd-flash")?.classList.remove("rd-flash");
    element.classList.add("rd-flash");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => element.classList.remove("rd-flash"), 1500);
  }
  requestPaint();
}

function contiguousMatches(text, exact) {
  const hits = [];
  let from = 0;
  while (from <= text.length) {
    const start = text.indexOf(exact, from);
    if (start < 0) break;
    hits.push(start);
    from = start + 1;
  }
  return hits;
}

function locate(note) {
  const preferred = byId.get(note.paragraphId);
  const {exact, prefix, suffix} = note.quote;
  if (!exact) return preferred && note.type === "bookmark" ? {...preferred, start: 0, end: 0} : null;
  const start = note.position?.start;
  if (preferred && Number.isInteger(start) && start >= 0 && preferred.text.slice(start, start + exact.length) === exact) {
    return {...preferred, start, end: start + exact.length};
  }
  const candidates = byChapter.get(note.chapter) || [];
  let best = null;
  let tied = false;
  for (const block of candidates) {
    for (const found of contiguousMatches(block.text, exact)) {
      const end = found + exact.length;
      const score = (block === preferred ? 10 : 0)
        + (prefix && block.text.slice(0, found).endsWith(prefix) ? 5 : 0)
        + (suffix && block.text.slice(end).startsWith(suffix) ? 5 : 0);
      if (!best || score > best.score) {
        best = {...block, start: found, end, score};
        tied = false;
      } else if (score === best.score) tied = true;
    }
  }
  return tied ? null : best;
}

function domRange(location) {
  const walker = document.createTreeWalker(location.element, NodeFilter.SHOW_TEXT);
  let startNode, endNode, startOffset, endOffset;
  let offset = 0;
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const next = offset + node.length;
    if (!startNode && next > location.start) {
      startNode = node;
      startOffset = location.start - offset;
    }
    if (next >= location.end) {
      endNode = node;
      endOffset = location.end - offset;
      break;
    }
    offset = next;
  }
  if (!startNode || !endNode) return null;
  const range = new Range();
  range.setStart(startNode, startOffset);
  range.setEnd(endNode, endOffset);
  return range;
}

function renderHighlights() {
  if (globalThis.CSS?.highlights && globalThis.Highlight) {
    const groups = new Map();
    CSS.highlights.clear();
    for (const note of visible) {
      const location = locations.get(note.id);
      if (!location || note.type === "bookmark") continue;
      const range = domRange(location);
      if (!range) continue;
      const name = "rd-" + note.author + "-" + note.color;
      if (!groups.has(name)) groups.set(name, new Highlight());
      groups.get(name).add(range);
    }
    for (const [name, group] of groups) CSS.highlights.set(name, group);
    return;
  }
  const affected = new Set();
  for (const mark of body.querySelectorAll(".rd-fallback-mark")) {
    affected.add(mark.parentNode);
    mark.replaceWith(...mark.childNodes);
  }
  for (const parent of affected) parent.normalize();
  const spans = new Map();
  for (const note of visible) {
    const location = locations.get(note.id);
    if (!location || note.type === "bookmark") continue;
    if (!spans.has(location.element)) spans.set(location.element, []);
    spans.get(location.element).push({...location, note});
  }
  for (const [element, ranges] of spans) {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const segments = [];
    let offset = 0;
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const next = offset + node.length;
      const hits = ranges.filter(range => range.start < next && range.end > offset);
      const boundaries = [...new Set([offset, next, ...hits.flatMap(range => [Math.max(offset, range.start), Math.min(next, range.end)])])].sort((a, b) => b - a);
      for (let i = 1; i < boundaries.length; i++) {
        const end = boundaries[i - 1], start = boundaries[i];
        const hit = hits.slice().reverse().find(range => range.start <= start && range.end >= end);
        if (hit) segments.push({node, start: start - offset, end: end - offset, note: hit.note});
      }
      offset = next;
    }
    for (const segment of segments) {
      const {node, start, end, note} = segment;
      node.splitText(end);
      const middle = node.splitText(start);
      const mark = document.createElement("mark");
      mark.className = "rd-fallback-mark";
      mark.dataset.color = note.color;
      mark.dataset.ai = String(note.author === "ai");
      middle.replaceWith(mark);
      mark.append(middle);
    }
  }
}

function shortText(text, max = 85) {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? flat.slice(0, max) + "…" : flat;
}

function noteLabel(note) {
  return shortText(note.note || note.quote.exact) || kinds[note.type];
}

function sourceLabel(note) {
  return note.author === "ai" ? note.editedByMe ? "AI · 已编辑" : "AI" : "我的";
}

function openNote(note, location = locations.get(note.id)) {
  if (!location) {
    notice("这条批注尚未定位，可在列表里查看或编辑。");
    return;
  }
  rememberReturn();
  current = note.id;
  if (innerWidth <= 820) setPanel(null);
  jump(location.element, true, note.type === "bookmark" ? null : domRange(location));
  requestPaint();
}

function refreshBookmarks(notes) {
  const next = new Set();
  for (const note of notes) {
    if (note.type !== "bookmark") continue;
    const location = locate(note);
    if (location) next.add(location.element);
  }
  for (const element of bookmarkBlocks) {
    if (!next.has(element)) element.removeAttribute("data-bookmarked");
  }
  for (const element of next) {
    if (!bookmarkBlocks.has(element)) element.setAttribute("data-bookmarked", "");
  }
  bookmarkBlocks = next;
}

function refreshNotes() {
  const all = allNotes();
  refreshBookmarks(all);
  const notes = all.filter(note =>
    (settings.showAI || note.author !== "ai")
    && (!$("source").value || note.author === $("source").value)
    && (!$("type").value || note.type === $("type").value)
    && (!$("color-filter").value || note.color === $("color-filter").value)
  );
  locations = new Map(notes.map(note => [note.id, locate(note)]));
  const order = note => locations.get(note.id)?.order ?? Infinity;
  visible = notes.sort((a, b) => order(a) === order(b)
    ? (locations.get(a.id)?.start ?? 0) - (locations.get(b.id)?.start ?? 0)
    : order(a) - order(b));
  renderHighlights();

  const cards = document.createDocumentFragment();
  const rail = document.createDocumentFragment();
  const margin = document.createDocumentFragment();
  listed = new Map();
  ticks = new Map();
  marginCards = [];
  let lastChapter = null;
  for (const note of visible) {
    const location = locations.get(note.id);
    const chapter = location?.element.closest(".rd-chapter").id ?? "unresolved";
    if (chapter !== lastChapter) {
      const heading = document.createElement("h3");
      heading.className = "rd-note-chapter";
      heading.textContent = chapterInfo.get(chapter)?.title ?? "待定位";
      cards.append(heading);
      lastChapter = chapter;
    }
    const row = document.createElement("div");
    row.className = "rd-note-card";
    row.dataset.color = note.color;
    const jumpButton = attrButton("", () => openNote(note), "rd-note-jump");
    const meta = document.createElement("small");
    meta.className = "rd-note-meta";
    meta.textContent = [sourceLabel(note), kinds[note.type], note.tag].filter(Boolean).join(" · ");
    const label = document.createElement("span");
    label.textContent = noteLabel(note);
    jumpButton.append(meta, label);
    if (note.note && note.quote.exact) {
      const quote = document.createElement("em");
      quote.textContent = shortText(note.quote.exact, 50);
      jumpButton.append(quote);
    }
    row.append(jumpButton, attrButton("编辑", () => openEditor(note), "rd-note-edit"));
    cards.append(row);
    listed.set(note.id, row);

    if (location) {
      const tick = attrButton("", () => openNote(note), "rd-tick");
      tick.title = noteLabel(note);
      tick.setAttribute("aria-label", noteLabel(note));
      tick.dataset.color = note.color;
      ticks.set(note.id, tick);
      rail.append(tick);

      const card = attrButton("", () => openEditor(note), "rd-margin-note");
      card.dataset.color = note.color;
      const caption = document.createElement("small");
      caption.textContent = [sourceLabel(note), kinds[note.type], note.tag].filter(Boolean).join(" · ");
      const title = document.createElement("span");
      title.textContent = noteLabel(note);
      card.append(caption, title);
      margin.append(card);
      marginCards.push({card, location});
    }
  }
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "rd-note-chapter";
    empty.textContent = all.length ? "当前筛选下没有批注。" : "选中文字即可划线或写笔记。";
    cards.append(empty);
  }
  $("note-count").textContent = String(visible.length);
  $("notes-list").replaceChildren(cards);
  $("rail").replaceChildren(rail);
  $("margin").replaceChildren(margin);
  setGeometry();
  requestPaint();
}

// Keep the reading DOM and its scroll position intact while reviewing.
let reviewEntries = null;
let reviewShown = 0;
let reviewShowAI;
let reviewScrollTop = 0;
const reviewBatch = 24;
const reviewGrid = $("review-grid");
let reviewWidth = 0;
let placedCards = 0;
let columnBottoms = [];

function layoutReviewCards() {
  if (!$("review").open) return;
  const width = reviewGrid.clientWidth;
  if (!width) return;
  const gap = 14;
  const columns = Math.max(1, Math.min(3, Math.floor((width + gap) / (255 + gap))));
  const cardWidth = (width - (columns - 1) * gap) / columns;
  if (width !== reviewWidth) {
    reviewWidth = width;
    placedCards = 0;
    columnBottoms = Array(columns).fill(0);
    reviewGrid.style.setProperty("--card-width", cardWidth + "px");
  }
  if (placedCards === reviewGrid.children.length) return;

  // Measure the new batch together before writing any positions.
  const batch = [];
  for (let i = placedCards; i < reviewGrid.children.length; i++) {
    const card = reviewGrid.children[i];
    batch.push({card, height: card.getBoundingClientRect().height});
  }
  for (const {card, height} of batch) {
    const column = columnBottoms.indexOf(Math.min(...columnBottoms));
    card.style.left = column * (cardWidth + gap) + "px";
    card.style.top = columnBottoms[column] + "px";
    columnBottoms[column] += height + gap;
  }
  placedCards = reviewGrid.children.length;
  reviewGrid.style.height = Math.max(0, Math.max(...columnBottoms) - gap) + "px";
}

if ("ResizeObserver" in window) {
  new ResizeObserver(() => layoutReviewCards()).observe(reviewGrid);
} else {
  window.addEventListener("resize", () => layoutReviewCards());
}
document.fonts?.addEventListener("loadingdone", () => {
  reviewWidth = 0;
  layoutReviewCards();
});

function appendReviewBatch() {
  const fragment = document.createDocumentFragment();
  const end = Math.min(reviewEntries.length, reviewShown + reviewBatch);
  for (; reviewShown < end; reviewShown++) {
    const {note, location} = reviewEntries[reviewShown];
    const card = attrButton("", () => {
      if (!location) {
        $("review-status").textContent = "这条笔记暂时无法定位到原文。";
        return;
      }
      $("review").close();
      openNote(note, location);
    }, "rd-review-card");
    const chapter = chapterInfo.get(location?.element.closest(".rd-chapter").id || note.chapter);
    const meta = document.createElement("small");
    meta.textContent = chapter?.title || "待定位";
    const main = document.createElement("strong");
    main.textContent = note.note || note.quote.exact || kinds[note.type];
    card.append(meta, main);
    if (note.note && note.quote.exact) {
      const quote = document.createElement("span");
      quote.className = "rd-review-quote";
      quote.textContent = note.quote.exact;
      card.append(quote);
    }
    const source = document.createElement("small");
    source.className = "rd-review-source";
    source.dataset.color = note.color;
    source.textContent = [sourceLabel(note), note.tag || kinds[note.type]].join(" · ");
    card.append(source);
    fragment.append(card);
  }
  reviewGrid.append(fragment);
  layoutReviewCards();
  $("review-more").hidden = reviewShown === reviewEntries.length;
}

$("review-toggle").onclick = () => {
  setPanel(null);
  $("selection").hidden = true;
  $("review-status").textContent = "";
  if (!reviewEntries || reviewShowAI !== settings.showAI) {
    reviewShowAI = settings.showAI;
    reviewEntries = allNotes()
      .filter(note => reviewShowAI || note.author !== "ai")
      .map(note => ({note, location: locations.get(note.id) || locate(note)}))
      .sort((a, b) => (a.location?.order ?? Infinity) - (b.location?.order ?? Infinity)
        || (a.location?.start ?? 0) - (b.location?.start ?? 0));
    reviewShown = 0;
    reviewScrollTop = 0;
    reviewWidth = 0;
    placedCards = 0;
    reviewGrid.style.height = "0px";
    reviewGrid.replaceChildren();
    $("review-count").textContent = reviewEntries.length + " 条";
    $("review-empty").hidden = reviewEntries.length > 0;
    appendReviewBatch();
  }
  $("review").showModal();
  layoutReviewCards();
  $("review-scroll").scrollTop = reviewScrollTop;
};
$("review-close").onclick = () => $("review").close();
$("review").addEventListener("close", () => {
  $("review-toggle").focus({preventScroll: true});
});
$("review-scroll").addEventListener("scroll", () => {
  if ($("review").open) reviewScrollTop = $("review-scroll").scrollTop;
}, {passive: true});
$("review-more").onclick = appendReviewBatch;
if ("IntersectionObserver" in window) {
  const moreCards = new IntersectionObserver(entries => {
    if ($("review").open && !$("review-more").hidden && entries.some(entry => entry.isIntersecting)) {
      appendReviewBatch();
    }
  }, {root: $("review-scroll"), rootMargin: "300px"});
  moreCards.observe($("review-more"));
}

function setGeometry() {
  const rail = $("rail");
  const extent = Math.max(1, scroll.scrollHeight);
  const markerStep = 6;
  const size = Math.max(0, rail.clientHeight);
  const placed = [];
  for (const [id, tick] of ticks) {
    const top = Math.max(5, Math.min(size - 5, absoluteTop(locations.get(id).element) / extent * size));
    placed.push({tick, top});
  }
  let previous = -markerStep;
  const step = Math.min(markerStep, Math.max(0, size - 10) / Math.max(1, placed.length - 1));
  for (const item of placed) {
    item.top = Math.max(item.top, previous + step);
    previous = item.top;
  }
  let next = size - 5 + step;
  for (let index = placed.length - 1; index >= 0; index--) {
    const item = placed[index];
    item.top = Math.min(item.top, next - step);
    item.tick.style.top = Math.max(0, item.top) + "px";
    next = item.top;
  }

  if (!$("margin").clientWidth) return;
  const marginTop = absoluteTop($("margin"));
  const measurements = marginCards.map(({card, location}) => ({
    card, anchor: absoluteTop(location.element) - marginTop, height: card.offsetHeight
  }));
  let bottom = 0;
  for (const {card, anchor, height} of measurements) {
    const top = Math.max(bottom, anchor);
    card.style.top = top + "px";
    bottom = top + height + 6;
  }
}

function requestPaint() {
  if (paintQueued) return;
  paintQueued = true;
  requestAnimationFrame(() => {
    paintQueued = false;
    const chapter = scrollChapter();
    const target = activeBlock(chapter);
    const targetOrder = byId.get(target?.dataset.pid)?.order ?? -1;
    $("location").textContent = chapterInfo.get(chapter.id).title + " · " + Math.round(scroll.scrollTop / Math.max(1, scroll.scrollHeight - scroll.clientHeight) * 100) + "%";
    // visible is sorted by paragraph; unresolved notes come last.
    let low = 0, high = visible.length;
    while (low < high) {
      const mid = Math.floor((low + high) / 2);
      const order = locations.get(visible[mid].id)?.order ?? Infinity;
      if (order <= targetOrder) low = mid + 1;
      else high = mid;
    }
    const closest = visible[low - 1];
    const nearby = closest && locations.get(closest.id)?.element.closest(".rd-chapter") === chapter;
    const active = current || (nearby ? closest.id : null);
    const former = $("notes-list").querySelector('[aria-current="true"]');
    const next = listed.get(active);
    if (former !== (next || null)) {
      former?.removeAttribute("aria-current");
      next?.setAttribute("aria-current", "true");
      if (next && !$("notes").hidden) {
        const panel = $("notes-list");
        const area = panel.getBoundingClientRect();
        const row = next.getBoundingClientRect();
        if (row.top < area.top || row.bottom > area.bottom) panel.scrollTop += row.top - area.top - 30;
      }
    }
    $("rail").querySelector('[aria-current="true"]')?.removeAttribute("aria-current");
    ticks.get(active)?.setAttribute("aria-current", "true");
    if (chapter.id === lastTocChapter) return;
    lastTocChapter = chapter.id;
    for (const [id, button] of tocEntries) {
      if (id === chapter.id) button.setAttribute("aria-current", "true");
      else button.removeAttribute("aria-current");
    }
  });
}

function setPanel(open) {
  for (const id of ["toc", "notes", "settings"]) {
    $(id).hidden = id !== open;
    $(id + "-toggle").setAttribute("aria-expanded", String(id === open));
  }
  requestPaint();
}

const tocList = document.createDocumentFragment();
const tocEntries = [];
let lastTocChapter = null;
for (const row of book.toc) {
  const target = document.getElementById(row.target);
  const button = attrButton(row.title, () => {
    rememberReturn();
    setPanel(null);
    current = null;
    jump(target);
  }, "rd-toc-entry");
  button.style.paddingInlineStart = 9 + Math.min(row.depth, 5) * 13 + "px";
  tocEntries.push([target?.closest(".rd-chapter")?.id, button]);
  tocList.append(button);
}
$("toc-list").append(tocList);
for (const id of ["toc", "notes", "settings"]) $(id + "-toggle").onclick = () => setPanel($(id).hidden ? id : null);
for (const button of document.querySelectorAll("[data-close]")) button.onclick = () => {
  setPanel(null);
  document.getElementById(button.dataset.close + "-toggle")?.focus();
};
for (const id of ["source", "type", "color-filter"]) $(id).onchange = () => { current = null; refreshNotes(); };

function applySettings() {
  document.body.dataset.theme = settings.theme;
  document.body.dataset.layout = settings.layout;
  const root = document.documentElement.style;
  root.setProperty("--font", {serif: "var(--serif)", sans: "var(--chrome)", mono: 'ui-monospace,"Songti SC",monospace'}[settings.font] || "var(--serif)");
  root.setProperty("--size", Number(settings.size) + "px");
  root.setProperty("--leading", Number(settings.leading));
  root.setProperty("--measure", Number(settings.width) + "px");
  for (const choice of themeChoices) choice.checked = choice.value === settings.theme;
  for (const id of ["font", "size", "leading", "width"]) $(id).value = settings[id];
  $("size-value").value = settings.size + " px";
  $("leading-value").value = Number(settings.leading).toFixed(2);
  $("width-value").value = settings.width + " px";
  $("show-ai").checked = settings.showAI;
  $("layout").textContent = settings.layout === "split" ? "双栏" : "单栏";
  $("layout").setAttribute("aria-pressed", String(settings.layout === "split"));
}

for (const choice of themeChoices) {
  choice.onchange = () => {
    if (!choice.checked) return;
    settings.theme = choice.value;
    applySettings();
    persist();
  };
}

for (const id of ["font", "size", "leading", "width"]) {
  $(id).oninput = () => {
    const position = positionSnapshot();
    settings[id] = $(id).value;
    applySettings();
    restorePosition(position);
    setGeometry();
    requestPaint();
    reading = positionSnapshot();
    persist();
  };
}
$("show-ai").onchange = () => {
  settings.showAI = $("show-ai").checked;
  refreshNotes();
  persist();
};
$("layout").onclick = () => {
  const position = positionSnapshot();
  settings.layout = settings.layout === "split" ? "single" : "split";
  applySettings();
  restorePosition(position);
  setGeometry();
  requestPaint();
  reading = positionSnapshot();
  persist();
};

function writeNote(note, deleting = false) {
  reviewEntries = null;
  changes.set(note.id, {note: deleting ? null : note, updatedAt: new Date().toISOString()});
  dirty = true;
  current = deleting ? null : note.id;
  pending = null;
  $("selection").hidden = true;
  window.getSelection()?.removeAllRanges();
  refreshNotes();
  persist();
  if (note.type === "bookmark") {
    notice(deleting ? "已删除书签" : "已添加书签");
  } else {
    notice(deleting ? "已删除批注" : "已保存 · 可在批注面板导出备份");
  }
}

function newNote(location, type, color) {
  const when = new Date().toISOString();
  const id = globalThis.crypto?.randomUUID?.() || "me-" + Date.now() + "-" + Math.random().toString(36).slice(2);
  const {element, start, end, text} = location;
  return {id, bookId: book.bookId, chapter: element.closest(".rd-chapter").id, paragraphId: element.dataset.pid,
    quote: {exact: text.slice(start, end), prefix: text.slice(Math.max(0, start - 32), start), suffix: text.slice(end, end + 32)},
    position: {start, end}, author: "me", type, tag: "", color, note: "", createdAt: when, updatedAt: when};
}

function openEditor(note, isNew = false) {
  editing = {...note};
  $("selection").hidden = true;
  $("editor-title").textContent = isNew ? "写笔记" : "编辑" + kinds[note.type];
  $("editor-quote").textContent = note.quote.exact || "当前位置书签";
  $("editor-text").value = note.note;
  $("editor-color").value = note.color;
  $("editor-delete").hidden = isNew;
  $("editor").showModal();
  $("editor-text").focus();
}

$("editor-close").onclick = () => $("editor").close();
$("editor").addEventListener("close", () => { editing = null; $("editor").removeAttribute("data-confirm-delete"); $("editor-delete").textContent = "删除"; });
$("editor-save").onclick = () => {
  if (!editing) return;
  const note = $("editor-text").value.trim();
  writeNote({...editing, type: editing.type === "bookmark" ? "bookmark" : note ? "note" : "highlight",
    color: $("editor-color").value, note, updatedAt: new Date().toISOString(),
    ...(editing.author === "ai" ? {editedByMe: true} : {})});
  $("editor").close();
};
$("editor-delete").onclick = () => {
  if (!editing) return;
  if (!$("editor").hasAttribute("data-confirm-delete")) {
    $("editor").setAttribute("data-confirm-delete", "");
    $("editor-delete").textContent = "确认删除";
    return;
  }
  writeNote(editing, true);
  $("editor").close();
};
$("mark").onclick = () => {
  const element = activeBlock();
  if (!element) return notice("当前页没有可添加书签的文字。");
  if (bookmarkBlocks.has(element)) return notice("这一段已添加书签。");
  const block = byId.get(element.dataset.pid);
  writeNote(newNote({...block, start: 0, end: Math.min(65, block.text.length)}, "bookmark", "purple"));
};

function selectedBlock() {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  const find = node => (node.nodeType === 1 ? node : node.parentElement)?.closest("[data-pid]");
  const element = find(range.startContainer);
  if (!element || !body.contains(element) || element !== find(range.endContainer)) return null;
  const startRange = new Range();
  startRange.selectNodeContents(element);
  startRange.setEnd(range.startContainer, range.startOffset);
  const start = startRange.toString().length;
  const exact = range.toString();
  const block = byId.get(element.dataset.pid);
  if (!exact.trim() || block.text.slice(start, start + exact.length) !== exact) return null;
  return {...block, start, end: start + exact.length, rect: range.getBoundingClientRect()};
}

function showSelection() {
  if ($("editor").open) return;
  pending = selectedBlock();
  $("selection").hidden = !pending;
  if (!pending) return;
  const bar = $("selection");
  bar.style.left = Math.max(8, Math.min(innerWidth - bar.offsetWidth - 8, pending.rect.left)) + "px";
  const above = pending.rect.top - bar.offsetHeight - 8;
  bar.style.top = Math.max(8, above > 5 ? above : pending.rect.bottom + 8) + "px";
}
let selectionTimer;
document.addEventListener("selectionchange", () => {
  clearTimeout(selectionTimer);
  selectionTimer = setTimeout(showSelection, 90);
});
document.addEventListener("pointerup", event => {
  if (!$("selection").contains(event.target) && body.contains(event.target)) setTimeout(showSelection, 0);
});
$("selection").addEventListener("pointerdown", event => event.preventDefault());
for (const [color, label] of Object.entries(palette)) {
  const swatch = attrButton("", () => { if (pending) writeNote(newNote(pending, "highlight", color)); }, "rd-swatch");
  swatch.setAttribute("aria-label", label + "色划线");
  swatch.title = label + "色划线";
  swatch.dataset.color = color;
  $("swatches").append(swatch);
}
$("new-note").onclick = () => { if (pending) openEditor(newNote(pending, "note", "yellow"), true); };

function download(text, extension, mime) {
  const link = document.createElement("a");
  const url = URL.createObjectURL(new Blob([text], {type: mime}));
  link.download = book.title.replace(/[\\/:*?"<>|]/g, "_") + ".annotations." + extension;
  link.href = url;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$("export").onclick = () => {
  const position = positionSnapshot();
  download(JSON.stringify({format: book.format, bookId: book.bookId, title: book.title, annotations: allNotes(),
    changes: Object.fromEntries(changes), settings, reading: position, exportedAt: new Date().toISOString()}, null, 2), "json", "application/json");
  reading = position;
  dirty = false;
  persist();
  notice("已生成 JSON 备份");
};

$("export-md").onclick = () => {
  const lines = ["# " + book.title + " · 批注", ""];
  const notes = allNotes().sort((a, b) => (locate(a)?.order ?? Infinity) - (locate(b)?.order ?? Infinity));
  for (const note of notes) {
    lines.push("## " + (chapterInfo.get(note.chapter)?.title || "待定位"),
      [sourceLabel(note), kinds[note.type], note.tag].filter(Boolean).join(" · "), "");
    if (note.quote.exact) lines.push(...note.quote.exact.split("\n").map(line => "> " + line), "");
    if (note.note) lines.push(note.note, "");
  }
  download(lines.join("\n"), "md", "text/markdown;charset=utf-8");
  notice("已导出 Markdown");
};

function validNote(note) {
  return note && typeof note.id === "string" && note.id && note.bookId === book.bookId
    && typeof note.chapter === "string" && typeof note.paragraphId === "string"
    && note.quote && ["exact", "prefix", "suffix"].every(field => typeof note.quote[field] === "string")
    && ["ai", "me"].includes(note.author) && Object.hasOwn(kinds, note.type) && Object.hasOwn(palette, note.color)
    && typeof note.note === "string";
}

function importBackup(data) {
  if (!data || data.bookId !== book.bookId || !Array.isArray(data.annotations)) throw new Error("请选择这一本书的批注 JSON。");
  reviewEntries = null;
  let bad = 0;
  let recovered = 0;
  for (const [id, record] of Object.entries(data.changes || {})) {
    if (!record || typeof record.updatedAt !== "string" || (record.note !== null && (!validNote(record.note) || record.note.id !== id))) {
      bad++;
      continue;
    }
    const local = changes.get(id);
    if (!local || record.updatedAt > local.updatedAt) {
      changes.set(id, record);
      recovered++;
    }
  }
  for (const note of data.annotations) {
    if (!validNote(note)) { bad++; continue; }
    if (!changes.has(note.id) && !baseline.has(note.id)) {
      changes.set(note.id, {note, updatedAt: typeof note.updatedAt === "string" ? note.updatedAt : ""});
      recovered++;
    }
  }
  if (data.settings && typeof data.settings === "object") {
    for (const [id, options] of Object.entries({theme: themeChoices.map(choice => choice.value), font: ["serif", "sans", "mono"], layout: ["split", "single"], showAI: [true, false]})) {
      if (options.includes(data.settings[id])) settings[id] = data.settings[id];
    }
    for (const [id, limits] of Object.entries({size: [14, 32], leading: [1.35, 2.45], width: [420, 900]})) {
      const value = Number(data.settings[id]);
      if (value >= limits[0] && value <= limits[1]) settings[id] = value;
    }
  }
  if (data.reading && Number.isFinite(data.reading.y)) reading = data.reading;
  applySettings();
  restorePosition(reading);
  refreshNotes();
  persist();
  notice("已恢复 " + recovered + " 项改动" + (bad ? " · " + bad + " 项格式无效" : ""));
}

$("import").onclick = () => $("import-file").click();
$("import-file").onchange = async () => {
  const file = $("import-file").files[0];
  if (!file) return;
  try {
    importBackup(JSON.parse(await file.text()));
  } catch (error) {
    notice(error instanceof SyntaxError ? "JSON 文件格式无效。" : error.message);
  } finally {
    $("import-file").value = "";
  }
};

$("return").onclick = () => {
  restorePosition(returnPosition);
  returnPosition = null;
  $("return").hidden = true;
  current = null;
  requestPaint();
};

body.addEventListener("click", event => {
  const link = event.target.closest('a[href^="#"]');
  if (!link || event.defaultPrevented || event.metaKey || event.ctrlKey) return;
  const target = document.getElementById(link.getAttribute("href").slice(1));
  if (!target) return;
  event.preventDefault();
  current = null;
  rememberReturn();
  jump(target);
});

document.addEventListener("keydown", event => {
  if ($("review").open) return;
  if (event.key === "Escape" && !$("editor").open) {
    setPanel(null);
    $("selection").hidden = true;
  }
  if (event.altKey && ["ArrowUp", "ArrowDown"].includes(event.key) && !$("editor").open) {
    const available = visible.filter(note => locations.get(note.id));
    if (!available.length) return;
    event.preventDefault();
    const index = available.findIndex(note => note.id === current || listed.get(note.id)?.getAttribute("aria-current") === "true");
    const next = index < 0 ? (event.key === "ArrowUp" ? available.length - 1 : 0)
      : Math.max(0, Math.min(available.length - 1, index + (event.key === "ArrowUp" ? -1 : 1)));
    openNote(available[next]);
  }
});

scroll.addEventListener("wheel", () => { userNavigated = true; current = null; }, {passive: true});
scroll.addEventListener("touchmove", () => { userNavigated = true; current = null; }, {passive: true});
scroll.addEventListener("keydown", () => { userNavigated = true; current = null; });
scroll.addEventListener("scroll", () => {
  requestPaint();
  $("selection").hidden = true;
  clearTimeout(storageTimer);
  storageTimer = setTimeout(() => {
    reading = positionSnapshot();
    persist();
  }, 240);
}, {passive: true});
window.addEventListener("pagehide", () => { reading = positionSnapshot(); persist(); });

function loadImages() {
  const images = [...body.querySelectorAll("[data-asset]")];
  const hydrate = element => {
    const asset = document.getElementById(element.dataset.asset);
    if (asset) element.setAttribute(element.tagName.toLowerCase() === "image" ? "href" : "src", asset.textContent);
  };
  if (!("IntersectionObserver" in window)) {
    for (const element of images) hydrate(element);
    return;
  }
  const observer = new IntersectionObserver(entries => {
    for (const {target, isIntersecting} of entries) {
      if (isIntersecting) {
        hydrate(target);
        observer.unobserve(target);
      }
    }
  }, {root: scroll, rootMargin: "900px 0px"});
  for (const element of images) observer.observe(element);
}

applySettings();
refreshNotes();
backupStatus();
const opening = reading;
if (opening) restorePosition(opening);
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", loadImages, {once: true});
} else {
  loadImages();
}
if ("ResizeObserver" in window) {
  let timer;
  new ResizeObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (!imagesReady && opening && !userNavigated) restorePosition(opening);
      setGeometry();
      requestPaint();
    }, 90);
  }).observe(body);
  setTimeout(() => { imagesReady = true; }, 2200);
}
window.addEventListener("resize", () => { setGeometry(); requestPaint(); });
requestPaint();
