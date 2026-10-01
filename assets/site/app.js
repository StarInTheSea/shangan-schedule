(() => {
  "use strict";

  const dataNode = document.querySelector("#schedule-data");
  const dataset = JSON.parse(dataNode.textContent || "{}");
  const exams = Array.isArray(dataset.exams) ? dataset.exams : [];
  const coverageScopes = Array.isArray(dataset.coverage_scopes) ? dataset.coverage_scopes : [];
  const events = exams.flatMap((exam) =>
    (exam.events || []).map((event) => ({ ...event, exam }))
  );

  const labels = {
    type: {
      national: "国考",
      provincial: "省考",
      municipal: "独立市考",
      selected_graduate: "选调生",
    },
    status: {
      confirmed: "已确定",
      tentative: "预计 / 初定",
      changed: "已调整",
    },
  };

  const elements = {
    timeline: document.querySelector("#timeline-view"),
    calendar: document.querySelector("#calendar-view"),
    calendarGrid: document.querySelector("#calendar-grid"),
    calendarMonth: document.querySelector("#calendar-month"),
    empty: document.querySelector("#empty-state"),
    resultCount: document.querySelector("#result-count"),
    year: document.querySelector("#year-filter"),
    type: document.querySelector("#type-filter"),
    region: document.querySelector("#region-filter"),
    stage: document.querySelector("#stage-filter"),
    refreshContext: document.querySelector("#refresh-context"),
    copyUpdatePrompt: document.querySelector("#copy-update-prompt"),
    copyStatus: document.querySelector("#copy-status"),
  };

  let activeView = "timeline";
  let defaultYear = "all";
  let defaultType = "all";
  const regionNames = new Map();
  const currentMonthParts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "numeric",
  }).formatToParts(new Date());
  const currentYear = Number(currentMonthParts.find((part) => part.type === "year").value);
  const currentMonth = Number(currentMonthParts.find((part) => part.type === "month").value);
  let calendarCursor = new Date(currentYear, currentMonth - 1, 1);

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function safeUrl(value) {
    try {
      const url = new URL(String(value));
      return ["https:", "http:"].includes(url.protocol) ? url.href : "#";
    } catch {
      return "#";
    }
  }

  function formatDay(value) {
    const date = new Date(`${value.slice(0, 10)}T00:00:00+08:00`);
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "long",
      day: "numeric",
      weekday: "short",
    }).format(date);
  }

  function formatTime(value) {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(new Date(value));
  }

  function formatShortDate(value, precision) {
    if (precision === "month") {
      const [year, month] = value.split("-");
      return `${year} 年 ${Number(month)} 月`;
    }
    if (precision === "datetime") {
      return `${formatDay(value)} ${formatTime(value)}`;
    }
    return formatDay(value);
  }

  function formatRange(event) {
    if (event.start === event.end) return formatShortDate(event.start, event.precision);
    if (event.precision === "month") {
      return `${formatShortDate(event.start, "month")} — ${formatShortDate(event.end, "month")}`;
    }
    if (event.precision === "datetime" && event.start.slice(0, 10) === event.end.slice(0, 10)) {
      return `${formatDay(event.start)} ${formatTime(event.start)}—${formatTime(event.end)}`;
    }
    if (event.precision === "datetime") {
      return `${formatShortDate(event.start, "datetime")} — ${formatShortDate(event.end, "datetime")}`;
    }
    return `${formatShortDate(event.start, "date")} — ${formatShortDate(event.end, "date")}`;
  }

  function monthKey(value) {
    return value.slice(0, 7);
  }

  function filteredEvents() {
    return events
      .filter(({ exam, stage }) => elements.year.value === "all" || String(exam.recruitment_year) === elements.year.value)
      .filter(({ exam }) => elements.type.value === "all" || exam.exam_type === elements.type.value)
      .filter(({ exam }) => elements.region.value === "all" || exam.region_code === elements.region.value)
      .filter(({ stage }) => elements.stage.value === "all" || stage === elements.stage.value)
      .sort((a, b) => a.start.localeCompare(b.start) || a.id.localeCompare(b.id));
  }

  function eventCard({ exam, ...event }, index) {
    const statusClass = event.status === "tentative" ? " is-tentative" : "";
    return `
      <article class="event-card" data-type="${escapeHtml(exam.exam_type)}" style="animation-delay:${Math.min(index * 45, 360)}ms">
        <a class="event-date" href="${escapeHtml(safeUrl(event.source.url))}" target="_blank" rel="noopener noreferrer" title="点击时间查看官方公告：${escapeHtml(event.source.title)}">
          ${escapeHtml(formatRange(event))}
          <small>${escapeHtml(event.label)}</small>
        </a>
        <div class="event-copy">
          <h3>${escapeHtml(exam.title)}</h3>
          <p>${escapeHtml(exam.region_name)} · ${escapeHtml(event.source.publisher)} · 公告发布于 ${escapeHtml(event.source.published_at)}</p>
        </div>
        <div class="event-meta">
          <div class="event-badges">
            <span class="badge">${escapeHtml(labels.type[exam.exam_type] || exam.exam_type)}</span>
            <span class="badge${statusClass}">${escapeHtml(labels.status[event.status] || event.status)}</span>
          </div>
          <a class="official-link" href="${escapeHtml(safeUrl(event.source.url))}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(event.source.title)}">
            查看官方公告
          </a>
        </div>
      </article>`;
  }

  function renderTimeline(filtered) {
    const groups = new Map();
    filtered.forEach((event) => {
      const key = monthKey(event.start);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(event);
    });
    elements.timeline.innerHTML = [...groups.entries()]
      .map(([key, group]) => {
        const [year, month] = key.split("-");
        return `
          <section class="month-group" aria-labelledby="month-${escapeHtml(key)}">
            <div class="month-heading">
              <strong id="month-${escapeHtml(key)}">${Number(month)}月</strong>
              <span>${escapeHtml(year)} / ${group.length} 项</span>
            </div>
            <div class="event-list">${group.map(eventCard).join("")}</div>
          </section>`;
      })
      .join("");
  }

  function calendarStart(cursor) {
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    const offset = (first.getDay() + 6) % 7;
    return new Date(first.getFullYear(), first.getMonth(), 1 - offset);
  }

  function localKey(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function eventOccursOnCalendarDay(event, key) {
    if (event.precision === "month") {
      const month = monthKey(key);
      return key.endsWith("-01") && month >= monthKey(event.start) && month <= monthKey(event.end);
    }
    return event.start.slice(0, 10) <= key && event.end.slice(0, 10) >= key;
  }

  function renderCalendar(filtered) {
    elements.calendarMonth.textContent = new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "long",
    }).format(calendarCursor);
    const start = calendarStart(calendarCursor);
    const days = Array.from({ length: 42 }, (_, index) => {
      const date = new Date(start);
      date.setDate(start.getDate() + index);
      return date;
    });
    elements.calendarGrid.innerHTML = days
      .map((date) => {
        const key = localKey(date);
        const dayEvents = filtered.filter((event) => eventOccursOnCalendarDay(event, key));
        const outside = date.getMonth() !== calendarCursor.getMonth() ? " is-outside" : "";
        return `
          <div class="calendar-day${outside}">
            <span class="calendar-day__number">${date.getDate()}</span>
            ${dayEvents.map((event) => `
              <a class="calendar-event" data-type="${escapeHtml(event.exam.exam_type)}" href="${escapeHtml(safeUrl(event.source.url))}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(`${event.exam.title} · ${event.label}`)}">
                ${escapeHtml(event.exam.region_name)} · ${escapeHtml(event.label)}
              </a>`).join("")}
          </div>`;
      })
      .join("");
  }

  function render() {
    const filtered = filteredEvents();
    elements.resultCount.textContent = `当前显示 ${filtered.length} 个官方日程事件`;
    elements.empty.hidden = filtered.length !== 0;
    elements.timeline.hidden = activeView !== "timeline" || filtered.length === 0;
    elements.calendar.hidden = activeView !== "calendar" || filtered.length === 0;
    renderTimeline(filtered);
    renderCalendar(filtered);
    renderCoverage();
    renderRefreshPrompt();
  }

  function initializeFilters() {
    const years = [...new Set([
      ...exams.map((exam) => String(exam.recruitment_year)),
      ...coverageScopes.map((scope) => String(scope.recruitment_year)),
    ])].sort().reverse();
    elements.year.innerHTML = years.map((year) => `<option value="${escapeHtml(year)}">${escapeHtml(year)} 年度</option>`).join("");
    if (years.length > 1) elements.year.insertAdjacentHTML("afterbegin", '<option value="all">全部年度</option>');
    defaultYear = years[0] || "all";
    elements.year.value = defaultYear;

    const newestScopeTypes = [...new Set(
      coverageScopes
        .filter((scope) => String(scope.recruitment_year) === defaultYear)
        .map((scope) => scope.exam_type)
    )];
    defaultType = newestScopeTypes.length === 1 ? newestScopeTypes[0] : "all";
    elements.type.value = defaultType;

    exams.forEach((exam) => regionNames.set(exam.region_code, exam.region_name));
    coverageScopes.forEach((scope) => {
      (scope.regions || []).forEach((region) => {
        regionNames.set(region.region_code, region.region_name);
      });
    });
    updateRegionOptions();
  }

  function availableRegions() {
    const matchesYear = (year) =>
      elements.year.value === "all" || String(year) === elements.year.value;
    const matchesType = (examType) =>
      elements.type.value === "all" || examType === elements.type.value;
    const available = new Map();
    coverageScopes
      .filter((scope) => matchesYear(scope.recruitment_year) && matchesType(scope.exam_type))
      .forEach((scope) => {
        (scope.regions || []).forEach((region) => {
          available.set(region.region_code, region.region_name);
        });
      });
    exams
      .filter((exam) => matchesYear(exam.recruitment_year) && matchesType(exam.exam_type))
      .forEach((exam) => available.set(exam.region_code, exam.region_name));
    return [...available.entries()].sort((a, b) => a[1].localeCompare(b[1], "zh-CN"));
  }

  function updateRegionOptions() {
    const previous = elements.region.value;
    const regions = availableRegions();
    elements.region.innerHTML = [
      '<option value="all">全国地区</option>',
      ...regions.map(
        ([code, name]) => `<option value="${escapeHtml(code)}">${escapeHtml(name)}</option>`
      ),
    ].join("");
    elements.region.value = previous === "all" || regions.some(([code]) => code === previous)
      ? previous
      : "all";
  }

  function matchingScope() {
    if (elements.year.value === "all") return null;
    const yearScopes = coverageScopes.filter(
      (scope) => String(scope.recruitment_year) === elements.year.value
    );
    if (elements.type.value !== "all") {
      return yearScopes.find((scope) => scope.exam_type === elements.type.value) || null;
    }
    return yearScopes.length === 1 ? yearScopes[0] : null;
  }

  function regionCoverage(coverage) {
    if (elements.region.value === "all" || !Array.isArray(coverage.regions)) {
      return coverage;
    }
    const region = coverage.regions.find(
      (item) => item.region_code === elements.region.value
    );
    if (!region) {
      return {
        expected: 1, verified: 0, candidate: 0, pending: 1, not_found: 0,
        checked_at: coverage.checked_at,
      };
    }
    return {
      expected: 1,
      verified: region.status === "verified" ? 1 : 0,
      candidate: region.status === "candidate" ? 1 : 0,
      pending: region.status === "pending" ? 1 : 0,
      not_found: region.status === "not_found" ? 1 : 0,
      checked_at: coverage.checked_at,
    };
  }

  function derivedCoverage() {
    if (elements.type.value === "all") {
      return regionCoverage(dataset.coverage || {});
    }
    const matching = exams.filter((exam) =>
      (elements.year.value === "all" || String(exam.recruitment_year) === elements.year.value)
      && exam.exam_type === elements.type.value
      && (exam.events || []).length > 0
    );
    const verifiedCodes = new Set(matching.map((exam) => exam.region_code));
    if (elements.region.value !== "all") {
      const verified = verifiedCodes.has(elements.region.value) ? 1 : 0;
      return {
        expected: 1, verified, candidate: 0, pending: 1 - verified, not_found: 0,
        checked_at: dataset.coverage?.checked_at || dataset.generated_at,
      };
    }
    const expected = verifiedCodes.size;
    const verified = Math.min(verifiedCodes.size, expected);
    return {
      expected, verified, candidate: 0, pending: Math.max(expected - verified, 0), not_found: 0,
      checked_at: dataset.coverage?.checked_at || dataset.generated_at,
    };
  }

  function currentCoverage() {
    const scope = matchingScope();
    return scope ? regionCoverage(scope) : derivedCoverage();
  }

  function renderCoverage() {
    const coverage = currentCoverage();
    const expected = Number(coverage.expected || 0);
    const verified = Number(coverage.verified || 0);
    const percent = expected ? Math.round((verified / expected) * 100) : 0;
    document.querySelector("#coverage-count").textContent = `${verified} / ${expected || "—"}`;
    document.querySelector("#coverage-meter-fill").style.width = `${percent}%`;
    const candidate = Number(coverage.candidate || 0);
    document.querySelector("#coverage-legend").innerHTML = [
      `<span style="color:var(--teal)">已核实 ${verified}</span>`,
      candidate ? `<span style="color:var(--blue)">候选待审 ${candidate}</span>` : "",
      `<span style="color:var(--gold)">待核实 ${Number(coverage.pending || 0)}</span>`,
      `<span style="color:var(--muted)">未发现 ${Number(coverage.not_found || 0)}</span>`,
    ].filter(Boolean).join("");
    const checked = coverage.checked_at || dataset.generated_at;
    document.querySelector("#last-checked").textContent = checked
      ? `最后核查 ${checked.slice(0, 10)}`
      : "尚无核查时间";
  }

  function updatePrompt() {
    const year = elements.year.value === "all" ? "全部招录年度" : `${elements.year.value} 年`;
    const region = elements.region.value === "all"
      ? "全国"
      : (regionNames.get(elements.region.value) || elements.region.value);
    const type = elements.type.value === "all"
      ? "公务员招录"
      : (labels.type[elements.type.value] || elements.type.value);
    return `请使用 shangan-schedule Skill，扫描官方来源白名单并更新 ${year}${region}${type}上岸时间表。请把工作文件保存在当前项目的 .shangan-schedule/ 中，先生成候选差异并等待我审核，不要预测尚未公布的日期；得到我明确批准后再晋升数据、生成并打开本地网站。`;
  }

  function renderRefreshPrompt() {
    const coverage = currentCoverage();
    const checked = coverage.checked_at || dataset.generated_at;
    elements.refreshContext.textContent = checked
      ? `当前筛选最后核查于 ${checked.slice(0, 10)}。页面不会自动联网，请及时让任意支持本 Skill 的 Agent 拉取最新官方公告。`
      : "当前筛选尚无核查时间。页面不会自动联网，请让任意支持本 Skill 的 Agent 拉取最新官方公告。";
  }

  document.querySelectorAll(".view-button").forEach((button) => {
    button.addEventListener("click", () => {
      activeView = button.dataset.view;
      document.querySelectorAll(".view-button").forEach((item) => {
        const active = item === button;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-selected", String(active));
      });
      render();
    });
  });

  [elements.year, elements.type].forEach((select) => {
    select.addEventListener("change", () => {
      updateRegionOptions();
      render();
    });
  });

  [elements.region, elements.stage].forEach((select) => {
    select.addEventListener("change", render);
  });

  document.querySelector("#reset-filters").addEventListener("click", () => {
    elements.year.value = defaultYear;
    elements.type.value = defaultType;
    updateRegionOptions();
    elements.region.value = "all";
    elements.stage.value = "all";
    render();
  });

  elements.copyUpdatePrompt.addEventListener("click", async () => {
    const prompt = updatePrompt();
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) {
        throw new Error("Clipboard API unavailable");
      }
      await navigator.clipboard.writeText(prompt);
      elements.copyStatus.textContent = "已复制，可粘贴给你的 Agent";
    } catch {
      elements.copyStatus.textContent = "复制失败，请手动选择提示词";
      elements.refreshContext.textContent = prompt;
    }
  });

  document.querySelector("#previous-month").addEventListener("click", () => {
    calendarCursor = new Date(calendarCursor.getFullYear(), calendarCursor.getMonth() - 1, 1);
    render();
  });

  document.querySelector("#next-month").addEventListener("click", () => {
    calendarCursor = new Date(calendarCursor.getFullYear(), calendarCursor.getMonth() + 1, 1);
    render();
  });

  initializeFilters();
  render();
})();
