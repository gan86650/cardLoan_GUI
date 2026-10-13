// 選人欄位：輸入伺服器暱稱、網站名稱或 Discord 使用者名稱（@handle）搜尋，從建議清單點選。
// 名單從 /people.json 載入一次；選到的人的 Discord ID 放在隱藏欄位送出。
(() => {
  const pickers = document.querySelectorAll(".picker");
  if (!pickers.length) return;

  let peoplePromise;
  const load = () => (peoplePromise ??= fetch("/people.json", { credentials: "same-origin" }).then((r) => r.json()));
  const norm = (s) => (s || "").toLowerCase();
  const MAX_RESULTS = 20;

  // 0：開頭就符合，1：中間符合，-1：不符合
  function score(person, q) {
    let best = -1;
    for (const field of [person.name, person.nick, person.handle]) {
      const i = norm(field).indexOf(q);
      if (i === 0) return 0;
      if (i > 0) best = 1;
    }
    return best;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  pickers.forEach((root) => {
    const input = root.querySelector(".picker-input");
    const hidden = root.querySelector(".picker-value");
    const list = root.querySelector(".picker-list");
    const exclude = (root.dataset.exclude || "").split(",").filter(Boolean);
    let results = [];
    let active = -1;

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      active = -1;
    }

    function highlight(i) {
      active = i;
      [...list.children].forEach((li, j) => li.classList.toggle("active", j === i));
      list.children[i]?.scrollIntoView({ block: "nearest" });
    }

    function choose(person) {
      hidden.value = person.id;
      input.value = person.name;
      input.setCustomValidity("");
      root.classList.add("chosen");
      close();
    }

    function render() {
      list.replaceChildren();
      if (!results.length) {
        list.append(el("li", "picker-empty", "找不到符合的人"));
      }
      results.forEach((p, i) => {
        const li = el("li", "picker-item");
        li.append(el("b", "", p.name));
        if (p.handle) li.append(el("span", "muted", "@" + p.handle));
        if (p.nick && p.nick !== p.name) li.append(el("span", "muted", p.nick));
        if (p.new) li.append(el("span", "picker-new", "還沒用過平台"));
        li.addEventListener("mousedown", (e) => {
          e.preventDefault();
          choose(p);
        });
        li.addEventListener("mousemove", () => highlight(i));
        list.append(li);
      });
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      highlight(results.length ? 0 : -1);
    }

    input.addEventListener("focus", load);
    input.addEventListener("input", async () => {
      hidden.value = "";
      root.classList.remove("chosen");
      const q = norm(input.value.trim());
      if (!q) return close();
      const people = await load();
      results = people
        .filter((p) => !exclude.includes(p.id))
        .map((p) => [score(p, q), p])
        .filter(([s]) => s >= 0)
        .sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name))
        .slice(0, MAX_RESULTS)
        .map(([, p]) => p);
      render();
    });
    input.addEventListener("keydown", (e) => {
      if (list.hidden) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        highlight(Math.min(active + 1, results.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        highlight(Math.max(active - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (results[active]) choose(results[active]);
      } else if (e.key === "Escape") {
        close();
      }
    });
    input.addEventListener("blur", close);

    input.form?.addEventListener("submit", (e) => {
      if (!hidden.value) {
        e.preventDefault();
        input.setCustomValidity("請從清單裡選一個人");
        input.reportValidity();
      }
    });
  });
})();
