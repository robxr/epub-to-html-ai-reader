// Build the guide only for books that carry a validated reading plan.
(() => {
  const plan = book.study;
  if (!plan) return;

  const dialog = $("study");
  const toggle = $("study-toggle");
  const draftKey = key + ":study:" + JSON.stringify([plan.stage, plan.goal]);
  const profileFormat = "epub-study-preferences/1";
  let seen = false;
  let lastSession = 0;
  let profile = {format: profileFormat, mode: plan.mode, sessionMinutes: plan.sessionMinutes, preferences: plan.preferences};
  const validProfile = value => value && value.format === profileFormat && typeof value.mode === "string"
    && value.mode.trim() && Number.isSafeInteger(value.sessionMinutes) && value.sessionMinutes > 0
    && typeof value.preferences === "string";
  try {
    const savedDraft = JSON.parse(localStorage.getItem(draftKey));
    if (validProfile(savedDraft?.profile)) profile = savedDraft.profile;
    seen = savedDraft?.seen === true;
    if (Number.isInteger(savedDraft?.lastSession) && plan.sessions[savedDraft.lastSession]) lastSession = savedDraft.lastSession;
  } catch { /* The downloadable preference file also works without storage. */ }

  function saveDraft() {
    try {
      localStorage.setItem(draftKey, JSON.stringify({seen, profile, lastSession}));
    } catch {
      $("study-status").textContent = "浏览器无法暂存。请导出偏好，以便保留这次调整。";
    }
  }

  function addText(parent, tag, text) {
    const child = document.createElement(tag);
    child.textContent = text;
    parent.append(child);
    return child;
  }

  function renderSession(session, notes, index) {
    const group = document.createElement("details");
    addText(group, "summary", session.title + " · 约 " + session.minutes + " 分钟");
    addText(group, "p", session.reason);
    let rendered = false;
    function fill() {
      if (rendered || !group.open) return;
      rendered = true;
      let count = 0;
      for (const id of session.noteIds) {
        const note = notes.get(id);
        if (!note || (!settings.showAI && note.author === "ai")) continue;
        count++;
        const entry = document.createElement("div");
        entry.className = "rd-study-entry";
        addText(entry, "small", [chapterInfo.get(note.chapter)?.title, note.tag].filter(Boolean).join(" · "));
        addText(entry, "blockquote", note.quote.exact);
        if (note.claim) addText(entry, "p", "作者观点 · " + note.claim);
        if (note.note) addText(entry, "p", explanationLabel(note) + " · " + note.note);
        entry.append(attrButton("读这段原文", () => {
          lastSession = index;
          saveDraft();
          dialog.close();
          openNote(note, locations.get(id) || locate(note));
        }));
        group.append(entry);
      }
      if (!count) addText(group, "p", "本节没有可显示的批注。可在外观中查看 AI 批注设置。");
    }
    group.addEventListener("toggle", fill);
    group.open = index === lastSession;
    fill();
    return group;
  }

  function openGuide() {
    setPanel(null);
    $("selection").hidden = true;
    const notes = new Map(allNotes().map(note => [note.id, note]));
    $("study-sessions").replaceChildren(...plan.sessions.map((session, i) => renderSession(session, notes, i)));
    dialog.showModal();
    dialog.scrollTop = 0;
    seen = true;
    saveDraft();
  }

  toggle.hidden = false;
  toggle.onclick = openGuide;
  $("study-close").onclick = () => dialog.close();
  dialog.addEventListener("keydown", event => event.stopPropagation());
  dialog.addEventListener("close", () => toggle.focus({preventScroll: true}));
  $("study-title").textContent = plan.stage === "preview" ? "试读与校准" : "精读路线";
  $("study-goal").textContent = plan.goal;
  $("study-meta").textContent = plan.mode + " · 每次 " + plan.sessionMinutes + " 分钟";
  if (plan.gaps.length) {
    const gaps = $("study-gaps");
    addText(gaps, "h3", "尚待解答的问题");
    const list = document.createElement("ul");
    for (const gap of plan.gaps) addText(list, "li", gap);
    gaps.append(list);
  }

  $("study-calibrate").open = plan.stage === "preview";
  const feedback = $("study-feedback");
  feedback.value = profile.preferences;
  feedback.oninput = () => { profile.preferences = feedback.value; saveDraft(); };
  $("study-export").onclick = () => {
    download(JSON.stringify(profile, null, 2), "preferences.json", "application/json");
    $("study-status").textContent = "已导出阅读偏好，可随下一次精读请求交给 AI 助手。";
  };
  $("study-import").onclick = () => $("study-file").click();
  $("study-file").onchange = async () => {
    const file = $("study-file").files[0];
    if (!file) return;
    try {
      const imported = JSON.parse(await file.text());
      if (!validProfile(imported)) throw new Error();
      profile = {format: profileFormat, mode: imported.mode, sessionMinutes: imported.sessionMinutes, preferences: imported.preferences};
      feedback.value = profile.preferences;
      $("study-status").textContent = "已载入偏好 · " + profile.mode + " · 每次 " + profile.sessionMinutes + " 分钟。可继续修改后导出。";
      saveDraft();
    } catch {
      $("study-status").textContent = "请选择从「校准阅读偏好」导出的 JSON 文件。";
    } finally {
      $("study-file").value = "";
    }
  };
  if (!seen && !location.hash && settings.showAI) openGuide();
})();
